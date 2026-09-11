"""A read-only process monitor with CPU, memory, and honest GPU views."""

from __future__ import annotations

import asyncio
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal
from textual.widgets import Button, DataTable, Input, Static

from . import processdata
from .gitdata import display_path


BLUE = "#6fa7ff"
GREEN = "#79d99b"
NEUTRAL = "#b8b8b8"
MUTED = "#777777"


def memory_size(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 1024**3:
        return f"{value / 1024**3:.1f}G"
    if value >= 1024**2:
        return f"{value / 1024**2:.0f}M"
    if value >= 1024:
        return f"{value / 1024:.0f}K"
    return f"{value}B"


def gpu_time(value: int | None) -> str:
    """Compact time reported by the current GPU contexts, not lifetime usage."""
    if value is None:
        return "—"
    if value < 1000:
        return f"{value}ns"
    if value < 1_000_000:
        return f"{value / 1000:.0f}µs"
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.1f}ms"
    seconds = value / 1_000_000_000
    if seconds < 60:
        return f"{seconds:.2f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


class ProcessName(Text):
    """Literal display text carrying the PID used for stable row sorting."""

    def __init__(self, name: str, pid: int) -> None:
        super().__init__(display_path(name), style=NEUTRAL, no_wrap=True, overflow="ellipsis")
        self.pid = pid


class UsageMeter(Static):
    """A current-usage bar with a fixed 0–100 scale, including after resizing."""

    def __init__(self, color: str, *, id: str) -> None:
        super().__init__(id=id, markup=False)
        self.percentage: float | None = None
        self.bar_color = color

    def render(self) -> Text:
        width = max(0, self.size.width)
        filled = round(max(0, min(100, self.percentage or 0)) / 100 * width)
        result = Text("━" * filled, style=self.bar_color)
        result.append("━" * (width - filled), style="#222222")
        return result

    def update_usage(self, value: float | None) -> None:
        self.percentage = value
        self.refresh()


class FooterButton(Button):
    """Mouse controls that keep keyboard focus in the table or filter."""

    can_focus = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Button assigns its own duration during construction; set the instance
        # value so rapid clicks never get swallowed by the active animation.
        self.active_effect_duration = 0


class ProcessMonitor(App[None]):
    ENABLE_COMMAND_PALETTE = False
    AUTO_FOCUS = "#process-table"
    CSS = """
    Screen { background: #000000; color: #b8b8b8; }
    * { scrollbar-size: 1 1; scrollbar-background: #000000;
        scrollbar-color: #333333; scrollbar-color-hover: #6fa7ff;
        scrollbar-background-hover: #000000; }
    #heading { height: 1; padding: 0 1; background: #111111;
        color: #dddddd; text-style: bold; text-wrap: nowrap; }
    .summary { height: 1; margin: 0 1; }
    #cpu-summary { margin: 1 1 0 1; }
    .summary Static { height: 1; width: 18; }
    .summary UsageMeter { height: 1; width: 1fr; margin-left: 1; background: #000000; }
    #modes { height: 1; margin: 1 1 0 1; }
    #modes Button { height: 1; min-width: 0; width: auto;
        padding: 0; margin-right: 1; border: none;
        background: #111111; color: #888888; text-style: none; }
    #modes Button.selected { background: #182b42; color: #d6e6ff; text-style: bold; }
    #modes Button:hover { background: #24394f; color: #ffffff; }
    #modes Button:focus { text-style: bold; }
    #filter { height: 1; padding: 0 1; margin: 1 1 0 1;
        background: #111111; border: none; color: #dddddd; }
    #filter:focus { border: none; }
    #note { height: auto; min-height: 1; margin: 1 1 0 1; color: #777777; }
    #process-table { height: 1fr; margin: 1 1 0 1; background: #000000;
        color: #b8b8b8; }
    DataTable > .datatable--header { background: #111111; color: #888888; text-style: bold; }
    DataTable > .datatable--cursor { background: #192330; text-style: none; }
    DataTable > .datatable--hover { background: #111111; }
    DataTable > .datatable--fixed { background: #000000; }
    #empty { height: 1fr; margin: 1; color: #999999; }
    #detail { height: 1; padding: 0 1; color: #999999;
        text-wrap: nowrap; text-overflow: ellipsis; }
    #actions { height: 1; padding: 0 1; background: #111111;
        grid-size: 4 1; grid-columns: 9 10 4 4; grid-rows: 1; }
    #actions.narrow { height: 2; grid-size: 2 2;
        grid-columns: 1fr 1fr; grid-rows: 1 1; }
    #actions Button { height: 1; width: 1fr; min-width: 0; padding: 0;
        margin: 0; border: none; background: #111111; color: #999999;
        text-style: none; }
    #actions Button:hover { background: #25303e; color: #d6e6ff; }
    """
    BINDINGS = [
        Binding("c", "cpu", "CPU", show=False),
        Binding("m", "memory", "Memory", show=False),
        Binding("g", "gpu", "GPU", show=False),
        Binding("slash", "filter", "Filter", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("r", "refresh_data", "Refresh", show=False),
    ]

    def __init__(self, *, auto_refresh: bool = True, initial_view: str = "cpu") -> None:
        super().__init__()
        if initial_view not in ("cpu", "gpu", "memory"):
            raise ValueError("Process view must be 'cpu', 'gpu', or 'memory'.")
        self.poll_enabled = auto_refresh
        self.metric_name = initial_view
        self.process_snapshot: Any = None
        self.filter_query = ""
        self._refresh_running = False
        self._column_spec: Any = None
        self._row_values: dict[str, tuple[Any, ...]] = {}
        self._process_lookup: dict[int, Any] = {}
        self._first_cpu_measurement = False
        self._view_signature: Any = None

    def compose(self) -> ComposeResult:
        yield Static("PROCESSES", id="heading", markup=False)
        with Horizontal(id="cpu-summary", classes="summary"):
            yield Static("CPU  …", id="cpu-value", markup=False)
            yield UsageMeter(BLUE, id="cpu-trend")
        with Horizontal(classes="summary"):
            yield Static("RAM  …", id="memory-value", markup=False)
            yield UsageMeter(GREEN, id="memory-trend")
        with Horizontal(id="modes"):
            yield Button("CPU", id="cpu", classes="selected" if self.metric_name == "cpu" else "")
            yield Button("GPU", id="gpu", classes="selected" if self.metric_name == "gpu" else "")
            yield Button("Memory", id="memory", classes="selected" if self.metric_name == "memory" else "")
        yield Input(placeholder="Filter process names…", id="filter")
        yield Static("Reading processes…", id="note", markup=False)
        yield DataTable(id="process-table", cursor_type="row", show_row_labels=False,
                        cursor_foreground_priority="renderable", cell_padding=1)
        yield Static("Reading processes…", id="empty", markup=False)
        yield Static("", id="detail", markup=False)
        with Grid(id="actions"):
            yield FooterButton("Filter", id="filter-button", tooltip="Filter process names (/); Escape clears")
            yield FooterButton("Refresh", id="refresh-button", tooltip="Refresh process measurements (r)")
            yield FooterButton("↑", id="scroll-up", tooltip="Scroll processes up")
            yield FooterButton("↓", id="scroll-down", tooltip="Scroll processes down")

    def on_mount(self) -> None:
        self.query_one("#filter").display = False
        self.query_one(DataTable).display = False
        self.query_one("#memory", Button).label = "Mem" if self.size.width < 25 else "Memory"
        self.query_one("#actions").set_class(self.size.width < 30, "narrow")
        if self.poll_enabled:
            self.reload_data()
            self.set_interval(2, self.reload_data)

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:
            self.query_one("#memory", Button).label = "Mem" if event.size.width < 25 else "Memory"
            self.query_one("#actions").set_class(event.size.width < 30, "narrow")
            self.call_after_refresh(self._render_processes)

    @work(group="process-snapshot", exit_on_error=False)
    async def reload_data(self) -> None:
        if self._refresh_running:
            return
        self._refresh_running = True
        try:
            value = await asyncio.to_thread(processdata.collect)
            self.apply_snapshot(value)
        except Exception as exc:
            self.query_one("#note", Static).update(Text("Refresh failed: " + display_path(str(exc)), style="#ff7f8a"))
            if self.process_snapshot is None:
                self.query_one("#empty", Static).update("Unable to read processes. Press r to retry.")
        finally:
            self._refresh_running = False

    def apply_snapshot(self, value: Any) -> None:
        previous = self.process_snapshot
        self._first_cpu_measurement = bool(previous is not None and previous.cpu_percent is None and value.cpu_percent is not None)
        self.process_snapshot = value
        self._process_lookup = {item.pid: item for item in value.processes}
        cpu = max(0.0, value.cpu_percent) if value.cpu_percent is not None else None
        memory_percent = value.memory_used / value.memory_total * 100 if value.memory_used is not None and value.memory_total else None
        self.query_one("#cpu-value", Static).update(Text(f"CPU  {cpu:.1f}%" if cpu is not None else "CPU  sampling…", style=BLUE))
        self.query_one("#memory-value", Static).update(Text(
            f"RAM  {memory_size(value.memory_used)} / {memory_size(value.memory_total)}", style=GREEN))
        self.query_one("#cpu-trend", UsageMeter).update_usage(cpu)
        self.query_one("#memory-trend", UsageMeter).update_usage(memory_percent)
        self._render_processes()

    def _columns(self) -> list[tuple[str, str, int]]:
        width = self.size.width
        metric = ("RAM", "memory", 7) if self.metric_name == "memory" else (self.metric_name.upper() + "%", self.metric_name, 6)
        columns = [metric]
        if width >= 42:
            if self.metric_name == "gpu":
                columns.append(("GPU time", "gpu_time", 9))
            else:
                columns.append(("CPU%", "cpu", 6) if self.metric_name == "memory" else ("RAM", "memory", 7))
        if width >= 30:
            columns.append(("PID", "pid", 6))
        # Include cell padding, the outside margin, and the vertical scrollbar.
        name_width = max(6, width - 3 - sum(column[2] + 2 for column in columns) - 2)
        return [("Process", "name", name_width), *columns]

    def _render_processes(self) -> None:
        snap = self.process_snapshot
        if snap is None:
            return
        table = self.query_one(DataTable)
        empty = self.query_one("#empty", Static)
        if self.metric_name == "gpu" and not snap.gpu_available:
            table.display = False
            empty.display = True
            empty.update("Per-process GPU usage is unavailable.\n\n" + display_path(
                snap.gpu_note or "GPU counters are unavailable on this hardware or macOS version."))
            self.query_one("#note", Static).update("GPU · no per-process measurements")
            self.query_one("#detail", Static).update("")
            return
        eligible = snap.processes
        if self.metric_name == "gpu":
            eligible = [item for item in snap.processes if item.gpu is not None or item.gpu_time_ns is not None]
        processes = [item for item in eligible if self.filter_query.casefold() in item.name.casefold()]
        if self.metric_name == "gpu":
            processes.sort(key=lambda item: (
                item.gpu is None, -(item.gpu if item.gpu is not None else 0),
                -(item.gpu_time_ns or 0), item.name.casefold(), item.pid,
            ))
        else:
            metric = lambda item: item.memory_bytes if self.metric_name == "memory" else item.cpu
            processes.sort(key=lambda item: (-metric(item), item.name.casefold(), item.pid))
        count = f"{len(processes)} of {len(eligible)}" if self.filter_query else str(len(processes))
        note = f"{count} processes"
        if self.metric_name == "cpu":
            note += " · sampling CPU…" if snap.cpu_percent is None else " · 100% CPU = one core"
        elif self.metric_name == "gpu":
            note += " · sampling GPU usage…" if snap.gpu_sampling else " · GPU time / elapsed time"
        self.query_one("#note", Static).update(note)
        table.display = bool(processes)
        empty.display = not bool(processes)
        empty.update("No matching process names." if self.filter_query else (
            "No processes are reporting GPU activity yet." if self.metric_name == "gpu"
            else "No process measurements are available yet."
        ))
        columns = self._columns()
        signature = (self.metric_name, tuple(columns), self.filter_query,
                     tuple((item.pid, item.name, item.cpu, item.memory_bytes, item.gpu, item.gpu_time_ns) for item in processes))
        if signature == self._view_signature:
            return
        self._view_signature = signature
        selected_pid = self.selected_pid()
        scroll = (table.scroll_x, table.scroll_y)
        old_row = table.cursor_row
        if self._first_cpu_measurement and self.metric_name == "cpu":
            # The first sample cannot measure CPU yet. Begin the live ranking
            # at its top instead of anchoring the initial alphabetical row.
            selected_pid, scroll, old_row = None, (0.0, 0.0), 0
            self._first_cpu_measurement = False
        if self._column_spec != columns:
            table.clear(columns=True)
            for label, key, width in columns:
                table.add_column(Text(label, style=MUTED), width=width, key=key)
            self._column_spec = columns
            self._row_values.clear()
        present = {str(item.pid) for item in processes}
        for key in set(self._row_values) - present:
            table.remove_row(key)
            del self._row_values[key]
        for item in processes:
            key = str(item.pid)
            values = {
                "name": ProcessName(item.name, item.pid),
                "cpu": Text(f"{item.cpu:.1f}", style=BLUE, justify="right"),
                "memory": Text(memory_size(item.memory_bytes), style=GREEN, justify="right"),
                "gpu": Text(f"{item.gpu:.1f}" if item.gpu is not None else "—", style=BLUE, justify="right"),
                "gpu_time": Text(gpu_time(item.gpu_time_ns), style=GREEN, justify="right"),
                "pid": Text(str(item.pid), style=MUTED, justify="right"),
            }
            fingerprint = tuple(values[col[1]].plain for col in columns)
            if key not in self._row_values:
                table.add_row(*(values[col[1]] for col in columns), key=key)
            elif fingerprint != self._row_values[key]:
                for _, col_key, _ in columns:
                    table.update_cell(key, col_key, values[col_key])
            self._row_values[key] = fingerprint
        rank = {item.pid: index for index, item in enumerate(processes)}
        if processes:
            table.sort("name", key=lambda value: rank[value.pid])
            target_row = rank.get(selected_pid, min(old_row, len(processes) - 1))
            table.move_cursor(row=target_row, scroll=False, animate=False)
            table.scroll_to(*scroll, animate=False, immediate=True)
            # DataTable's cursor watcher may schedule its own later scroll.
            table.call_after_refresh(table.scroll_to, *scroll, animate=False, immediate=True)
            self._show_detail(processes[target_row].pid)
            if self.focused is None:
                table.focus()
        else:
            self.query_one("#detail", Static).update("")

    def selected_pid(self) -> int | None:
        table = self.query_one(DataTable)
        if table.row_count and table.is_valid_row_index(table.cursor_row):
            return int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        return None

    def _show_detail(self, pid: int) -> None:
        item = self._process_lookup.get(pid)
        if item:
            text = Text(f"PID {pid} · ", style=MUTED)
            detail = self.query_one("#detail", Static)
            if self.metric_name == "gpu":
                reported = gpu_time(item.gpu_time_ns)
                if self.size.width >= 42:
                    text.append("Reported GPU time: ", style=MUTED)
                text.append(reported, style=GREEN)
                detail.tooltip = Text(
                    f"{display_path(item.name)}\nPID {pid}\nReported GPU time: {reported}\n"
                    "Reported time can decrease when a process closes GPU work.", style=NEUTRAL)
            else:
                text.append(display_path(item.name), style=NEUTRAL)
                detail.tooltip = None
            detail.update(text)

    @on(DataTable.RowHighlighted)
    def highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value:
            self._show_detail(int(event.row_key.value))

    @on(Button.Pressed, "#cpu")
    def action_cpu(self) -> None:
        self._set_metric("cpu")

    @on(Button.Pressed, "#memory")
    def action_memory(self) -> None:
        self._set_metric("memory")

    @on(Button.Pressed, "#gpu")
    def action_gpu(self) -> None:
        self._set_metric("gpu")

    def _set_metric(self, metric: str) -> None:
        self.metric_name = metric
        for name in ("cpu", "memory", "gpu"):
            self.query_one("#" + name, Button).set_class(name == metric, "selected")
        self._render_processes()
        if self.query_one(DataTable).display:
            self.query_one(DataTable).focus()

    @on(Button.Pressed, "#refresh-button")
    def action_refresh_data(self) -> None:
        self.reload_data()

    @on(Button.Pressed, "#filter-button")
    def toggle_filter(self) -> None:
        if self.query_one("#filter", Input).display:
            self.action_clear_filter()
        else:
            self.action_filter()

    @on(Button.Pressed, "#scroll-up")
    def scroll_processes_up(self) -> None:
        self._scroll_processes(-1)

    @on(Button.Pressed, "#scroll-down")
    def scroll_processes_down(self) -> None:
        self._scroll_processes(1)

    def _scroll_processes(self, direction: int) -> None:
        table = self.query_one(DataTable)
        if table.display:
            table.scroll_relative(y=direction * 3, animate=False, immediate=True)

    def action_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.display = True
        self.query_one("#filter-button", Button).label = "Clear"
        field.focus()

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self.filter_query = event.value.strip()
        self._render_processes()

    def action_clear_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.value = ""
        field.display = False
        self.query_one("#filter-button", Button).label = "Filter"
        self.filter_query = ""
        self._render_processes()
        if self.query_one(DataTable).display:
            self.query_one(DataTable).focus()


def run_processes(initial_view: str = "cpu") -> None:
    ProcessMonitor(initial_view=initial_view).run()

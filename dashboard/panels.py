"""Small, read-only project views for the dashboard's two left panes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.text import Text
from rich.style import Style
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Grid
from textual.widgets import Button, RichLog, Static, Tree
from textual.widgets.tree import TreeNode

from . import gitdata


BACKGROUND = "#000000"
NEUTRAL = "#b6b6b6"
MUTED = "#808080"
TIMESTAMP = "#707070"
BLUE = "#6fa7ff"
GREEN = "#79d99b"
RED = "#ff7f8a"
STATUS_COLORS = {"A": GREEN, "M": BLUE, "D": RED, "R": BLUE, "T": BLUE}


@dataclass(frozen=True)
class Entry:
    key: str
    change: Any = None
    commit: Any = None


class PanelActionButton(Button):
    """Mouse actions keep keyboard navigation in the tree or diff beneath them."""

    can_focus = False

    def on_mount(self) -> None:
        # Textual ignores clicks during this effect. Open changes to Back
        # immediately, so it must already accept the user's next click.
        self.active_effect_duration = 0


class ProjectTree(Tree[Entry]):
    """Preserve filename styling even when the row has keyboard focus."""

    BINDINGS = [
        Binding("left", "fold", show=False),
        Binding("right", "unfold", show=False),
        Binding("shift+left", "pan_left", "Scroll left", show=False),
        Binding("shift+right", "pan_right", "Scroll right", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
    ]

    def action_pan_left(self) -> None:
        self.scroll_relative(x=-8, animate=False)

    def action_pan_right(self) -> None:
        self.scroll_relative(x=8, animate=False)

    def render_label(self, node: TreeNode[Entry], base_style: Style, style: Style) -> Text:
        if node.data is not None and node.data.commit is not None:
            return commit_label(node.data.commit, max(1, self.scrollable_content_region.width), style)
        return super().render_label(node, base_style, style)

    def action_fold(self) -> None:
        node = self.cursor_node
        if node is not None:
            if node.allow_expand and node.is_expanded:
                node.collapse()
            elif node.parent is not None:
                self.move_cursor(node.parent)

    def action_unfold(self) -> None:
        node = self.cursor_node
        if node is not None and node.allow_expand:
            if node.is_expanded and node.children:
                self.move_cursor(node.children[0])
            else:
                node.expand()


def commit_timestamp(value: str) -> str:
    """Show the commit's recorded time in this Mac's local timezone."""
    if not value:
        return ""
    try:
        when = datetime.fromisoformat(value).astimezone()
    except ValueError:
        return ""
    date = when.strftime("%b %d" if when.year == datetime.now().year else "%b %d %Y")
    hour = when.hour % 12 or 12
    period = "AM" if when.hour < 12 else "PM"
    return f"{date} {hour}:{when.minute:02d} {period}"


def commit_label(commit: Any, width: int, style: Style = Style()) -> Text:
    """Reserve a quiet timestamp column without adding another row."""
    stamp = commit_timestamp(commit.date)
    label = Text(style=style, no_wrap=True)
    # At narrower widths, prioritize the message and timestamp over the hash.
    if width >= 42 or not stamp:
        label.append(commit.sha[:7] + "  ", style=MUTED)
    label.append(gitdata.display_path(commit.subject), style=NEUTRAL)
    if not stamp:
        label.truncate(width, overflow="ellipsis")
        return label
    available = max(0, width - len(stamp) - 2)
    if available < 1:
        return Text(stamp[-width:], style=Style(color=TIMESTAMP, bold=False))
    label.truncate(available, overflow="ellipsis")
    label.append(" " * (width - label.cell_len - len(stamp)))
    label.append(stamp, style=Style(color=TIMESTAMP, bold=False))
    return label


def file_label(change: Any, name: str | None = None) -> Text:
    """Only the changed leaf gets a status color; ancestry stays neutral."""
    label = Text(
        gitdata.display_path(name if name is not None else change.path),
        style=STATUS_COLORS.get(change.status, BLUE),
    )
    if change.uncommitted:
        label.append(" *", style=MUTED)
    if change.binary:
        label.append("  binary", style=MUTED)
    if change.old_path:
        label.append("  ← " + gitdata.display_path(change.old_path), style=MUTED)
    return label


def totals_text(changes: list[Any] | tuple[Any, ...], width: int = 0) -> Text:
    added = sum(item.added for item in changes)
    deleted = sum(item.deleted for item in changes)
    result = Text()
    result.append(f" +{added:,} ", style=f"bold {GREEN} on #153329")
    result.append(" ")
    result.append(f" −{deleted:,} ", style=f"bold {RED} on #3b222b")
    result.append(f"  {len(changes)} files", style=MUTED)
    result.append(" " * max(3, width - result.cell_len - len("Added · Modified · Deleted · Unchanged")))
    result.append("Added", style=GREEN)
    result.append(" · ", style=MUTED)
    result.append("Modified", style=BLUE)
    result.append(" · ", style=MUTED)
    result.append("Deleted", style=RED)
    result.append(" · ", style=MUTED)
    result.append("Unchanged", style=NEUTRAL)
    return result


def diff_text(raw: str, path: str | None = None) -> Text:
    from .highlighting import highlight_diff
    lines = gitdata.safe_text(raw).splitlines()
    bounded = [line[:3000] + (" …" if len(line) > 3000 else "") for line in lines[:10000]]
    result = highlight_diff(bounded, path, (GREEN, RED, BLUE, MUTED, NEUTRAL))
    if len(lines) > 10000:
        result.append("\nPreview truncated after 10,000 lines.\n", style=MUTED)
    return result


class DashboardPanel(App[None]):
    """One independent pane; tmux owns placement, resizing, and the real shell."""

    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: #000000; color: #b6b6b6; }
    * { scrollbar-size: 1 1; scrollbar-background: #000000;
        scrollbar-color: #333333; scrollbar-color-hover: #6fa7ff;
        scrollbar-background-hover: #000000; }
    #heading { height: 1; padding: 0 1; text-style: bold;
        background: #101010; color: #e0e0e0; text-wrap: nowrap;
        text-overflow: ellipsis; }
    #subtitle { height: 1; padding: 0 1; color: #808080;
        text-wrap: nowrap; text-overflow: ellipsis; }
    #modes { height: 1; margin: 1 1 0 1; }
    #modes Button { height: 1; min-width: 0; width: auto;
        padding: 0 1; margin: 0 1 0 0; border: none;
        background: #141414; color: #999999; text-style: none; }
    #modes Button.selected { background: #303030; color: #ffffff;
        text-style: bold; }
    #modes Button:hover { background: #3a3a3a; color: #ffffff; }
    #modes Button:focus { text-style: bold; }
    #totals { height: auto; min-height: 1; margin: 1 1 0 1; }
    #content { height: 1fr; margin: 1 0 0 0; padding: 0 1;
        overflow-x: auto; overflow-y: auto; }
    Tree { background: #000000; color: #b6b6b6; }
    Tree > .tree--guides { color: #3a3a3a; }
    Tree > .tree--guides-hover { color: #606060; }
    Tree > .tree--guides-selected { color: #606060; }
    Tree > .tree--cursor { background: #242424; text-style: bold; }
    Tree > .tree--highlight-line { background: #141414; }
    #empty { height: 1fr; margin: 1; color: #999999; }
    #preview { height: 1fr; padding: 0 1; margin-top: 1;
        background: #000000; }
    #actions { grid-size: 4 1; grid-columns: 1fr 1fr 1fr 1fr; grid-rows: 1; height: 1; padding: 0 1; background: #080808; }
    #actions.narrow { height: 2; grid-size: 2 2; grid-columns: 1fr 1fr; grid-rows: 1 1; }
    #actions Button { height: 1; min-width: 0; width: 1fr;
        padding: 0; margin: 0; border: none; text-wrap: nowrap; text-overflow: ellipsis;
        background: #202020; color: #b6b6b6; text-style: none; }
    #actions Button:hover { background: #3a3a3a; color: #ffffff; }
    #actions Button:disabled { background: #101010; color: #606060; }
    """
    BINDINGS = [
        Binding("a", "all_changes", "All changes", show=False),
        Binding("c", "committed", "Committed", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("b", "back", "Back", show=False),
        Binding("r", "refresh_data", "Refresh", show=False),
    ]

    def __init__(
        self,
        kind: str,
        root: str,
        base_override: str | None = None,
        *,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__()
        if kind not in ("files", "commits"):
            raise ValueError("Panel kind must be 'files' or 'commits'.")
        self.kind = kind
        self.project_root = root
        self.base_override = base_override
        self.poll_enabled = auto_refresh
        self.committed = False
        self.current_snapshot: Any = None
        self._signature: Any = None
        self._refresh_running = False
        self.formatted_preview = True
        self.split_preview = False
        self._preview_raw = None
        self._preview_path = None
        self._preview_open = False
        self._preview_entry: Entry | None = None
        self._preview_generation = 0
        self.entry_nodes: dict[str, TreeNode[Entry]] = {}

    def compose(self) -> ComposeResult:
        yield Static("COMMITS" if self.kind == "commits" else "FILE CHANGES", id="heading", markup=False)
        yield Static("Reading project…", id="subtitle", markup=False)
        with Horizontal(id="modes"):
            yield Button("All changes", id="all", classes="selected")
            yield Button("Committed", id="committed")
        yield Static("", id="totals", markup=False)
        tree = ProjectTree(Text(""), Entry("/"), id="content")
        tree.auto_expand = False
        tree.guide_depth = 2
        tree.show_guides = self.kind == "files"
        tree.show_root = self.kind == "files"
        yield tree
        yield Static("Reading project…", id="empty", markup=False)
        yield RichLog(min_width=1, max_lines=10002, wrap=False, markup=False,
                      highlight=False, auto_scroll=False, id="preview")
        with Grid(id="actions"):
            yield PanelActionButton("Open", id="open-entry", tooltip="Open selected item · Enter")
            yield PanelActionButton("Formatted", id="format-preview", tooltip="Toggle formatted / original preview")
            yield PanelActionButton("Stacked", id="split-preview", tooltip="Toggle stacked / side-by-side diff")
            yield PanelActionButton("Refresh", id="refresh", tooltip="Refresh this view · R")

    def on_mount(self) -> None:
        self.query_one("#actions").set_class(self.size.width < 48, "narrow")
        self.query_one("#modes").display = self.kind == "files"
        self.query_one("#totals").display = self.kind == "files"
        self.query_one("#preview").display = False
        self.query_one("#content").display = False
        self._update_controls()
        if self.poll_enabled:
            self.reload_data()
            self.set_interval(2, self.reload_data)

    def _update_controls(self) -> None:
        self.query_one("#split-preview").display = self._preview_open
        split_button = self.query_one("#split-preview", Button)
        split_button.disabled = self._is_added_preview()
        split_button.label = "New file" if split_button.disabled else ("Split" if self.split_preview else "Stacked")
        self.query_one("#format-preview").display = self._preview_open and self.kind == "files"
        button = self.query_one("#open-entry", Button)
        button.label = "Back" if self._preview_open else "Open"
        button.tooltip = "Back to the list · B / Esc" if self._preview_open else "Open selected item · Enter"
        node = self.query_one(ProjectTree).cursor_node
        can_open = node is not None and (
            bool(node.children) or (node.data is not None and (
                node.data.change is not None or node.data.commit is not None
            ))
        )
        button.disabled = not self._preview_open and not (
            self.query_one("#content").display and can_open
        )

    def on_resize(self, event: events.Resize) -> None:
        # Keep both mode buttons reachable when the user makes the pane narrow.
        if self.is_mounted:
            self.query_one("#all", Button).label = "All" if event.size.width < 31 else "All changes"
            self.query_one("#actions").set_class(event.size.width < 48, "narrow")
            if self._preview_open and self.split_preview:
                self.call_after_refresh(self.render_preview)

    @work(group="snapshot", exit_on_error=False)
    async def reload_data(self, *, refresh_preview: bool = False) -> None:
        if self._refresh_running:
            return
        self._refresh_running = True
        try:
            result = await asyncio.to_thread(gitdata.snapshot, self.project_root, self.base_override)
            self.apply_snapshot(result)
            if refresh_preview and self._preview_open and self._preview_entry is not None:
                entry = self._preview_entry
                # Use the freshly scanned rename/status information when present.
                if entry.change is not None:
                    changes = result.committed_files if self.committed else result.all_files
                    change = next((item for item in changes if item.path == entry.key), entry.change)
                    entry = Entry(entry.key, change=change)
                    self._preview_entry = entry
                self._preview_raw = None
                self._preview_generation += 1
                self.load_preview(entry, result, self.committed, self._preview_generation)
        except Exception as exc:
            self.query_one("#subtitle", Static).update(
                "Refresh failed: " + gitdata.display_path(str(exc))
            )
            if self.current_snapshot is None:
                self.query_one("#empty", Static).update("Unable to read this project. Press r to retry.")
        finally:
            self._refresh_running = False

    @on(Button.Pressed, "#refresh")
    def action_refresh_data(self) -> None:
        self.reload_data(refresh_preview=True)

    def apply_snapshot(self, value: Any) -> None:
        previous = self.current_snapshot
        self.current_snapshot = value
        if previous is not None and previous.branch != value.branch:
            self.action_back()
            self._signature = None
        self._render_snapshot()

    def _render_snapshot(self) -> None:
        snap = self.current_snapshot
        if snap is None:
            return
        branch = gitdata.display_path(snap.branch or "detached HEAD")
        base = gitdata.display_path(snap.base or "main")
        heading = Text("COMMITS" if self.kind == "commits" else "FILE CHANGES")
        if self.kind == "commits":
            heading.append("  incomplete" if snap.error else f"  {len(snap.commits)} ahead", style=RED if snap.error else BLUE)
        self.query_one("#heading", Static).update(heading)
        if not self._preview_open:
            self.query_one("#subtitle", Static).update(Text(f"{branch} · vs {base}", style=MUTED))
        changes = snap.committed_files if self.committed else snap.all_files
        if self.kind == "files":
            self.query_one("#totals", Static).update(
                Text("Totals unavailable · refresh failed", style=RED)
                if snap.error else totals_text(changes, self.size.width - 2)
            )
        error = str(snap.error) if snap.error else None
        if error:
            self.query_one("#subtitle", Static).update(Text(gitdata.display_path(error), style=RED))
        items = snap.commits if self.kind == "commits" else changes
        signature = (self.kind, self.committed, snap.root, snap.branch, snap.base, repr(items), error)
        if signature == self._signature:
            return
        self._signature = signature
        tree = self.query_one("#content", ProjectTree)
        scroll = (tree.scroll_x, tree.scroll_y)
        state = {key: node.is_expanded for key, node in self.entry_nodes.items()}
        selected = tree.cursor_node.data.key if tree.cursor_node and tree.cursor_node.data else "/"
        tree.reset(Text(gitdata.display_path(Path(snap.root).name) + "/", style=NEUTRAL), Entry("/"))
        self.entry_nodes = {"/": tree.root}
        if self.kind == "files":
            self._build_files(tree, changes, state)
        else:
            for commit in items:
                label = Text(commit.sha[:7] + "  ", style=MUTED)
                label.append(gitdata.display_path(commit.subject), style=NEUTRAL)
                self.entry_nodes[commit.sha] = tree.root.add_leaf(label, Entry(commit.sha, commit=commit))
        if state.get("/", True):
            tree.root.expand()
        node = self.entry_nodes.get(selected)
        if node is None or (self.kind == "commits" and node is tree.root):
            node = tree.root.children[0] if tree.root.children else tree.root
        # New TreeNode line numbers are assigned during the next layout pass.
        # Restoring the cursor before that pass would move it back to the root.
        self.call_after_refresh(self._restore_tree_position, node, scroll)
        empty = self.query_one("#empty", Static)
        if error and not items:
            empty.update("Cannot compare this project yet.\n\n" + gitdata.display_path(error))
        elif self.kind == "commits":
            empty.update(f"No commits ahead of {base}.\n\nNew branch commits will appear here.")
        elif self.committed:
            empty.update(f"No committed changes against {base}.\n\nChoose All changes to see unfinished work.")
        else:
            empty.update(f"No changes against {base}.\n\nChanged files will appear in their folders.")
        if not self._preview_open:
            tree.display = bool(items)
            empty.display = not bool(items)
            if self.focused is None or self.focused is empty:
                tree.focus()
        self._update_controls()

    def _restore_tree_position(self, node: TreeNode[Entry], scroll: tuple[float, float]) -> None:
        if node.data and self.entry_nodes.get(node.data.key) is node:
            tree = self.query_one("#content", ProjectTree)
            tree.move_cursor(node)
            tree.scroll_to(*scroll, animate=False)
            self._update_controls()

    def _build_files(self, tree: ProjectTree, changes: Any, state: dict[str, bool]) -> None:
        # A trie also handles a former file replaced by a directory at the same path.
        trie: dict[str, Any] = {"children": {}}
        for change in changes:
            entry = trie
            for part in change.path.split("/"):
                entry = entry["children"].setdefault(part, {"children": {}})
            entry["change"] = change

        def add_children(parent: TreeNode[Entry], entry: dict[str, Any], prefix: str) -> None:
            children = entry["children"]
            for name in sorted(children, key=lambda n: (not bool(children[n]["children"]), n.casefold(), n)):
                child = children[name]
                path = prefix + name
                change = child.get("change")
                label = file_label(change, name) if change else Text(gitdata.display_path(name) + "/", style=NEUTRAL)
                node = parent.add(label, Entry(path, change=change),
                                  allow_expand=bool(child["children"]),
                                  expand=state.get(path, True) and bool(child["children"]))
                self.entry_nodes[path] = node
                add_children(node, child, path + "/")

        add_children(tree.root, trie, "")

    @on(Button.Pressed, "#all")
    def action_all_changes(self) -> None:
        self._set_mode(False)

    @on(Button.Pressed, "#committed")
    def action_committed(self) -> None:
        self._set_mode(True)

    def _set_mode(self, committed: bool) -> None:
        if self.kind != "files":
            return
        self.action_back()
        self.committed = committed
        self.query_one("#all", Button).set_class(not committed, "selected")
        self.query_one("#committed", Button).set_class(committed, "selected")
        self._render_snapshot()
        self.query_one("#content", ProjectTree).focus()

    @on(Tree.NodeSelected, "#content")
    def select_entry(self, event: Tree.NodeSelected[Entry]) -> None:
        self._activate_entry(event.node)

    @on(Tree.NodeHighlighted, "#content")
    def highlighted_entry(self) -> None:
        self._update_controls()

    @on(Button.Pressed, "#open-entry")
    def open_or_back(self) -> None:
        if self._preview_open:
            self.action_back()
            return
        tree = self.query_one(ProjectTree)
        node = tree.cursor_node
        if tree.display and node is not None:
            tree.focus()
            self._activate_entry(node)

    def _activate_entry(self, node: TreeNode[Entry]) -> None:
        if node.allow_expand:
            node.toggle()
        elif node.data and (node.data.change is not None or node.data.commit is not None):
            self.open_preview(node.data)

    def open_preview(self, entry: Entry) -> None:
        if self.current_snapshot is None:
            return
        self._preview_open = True
        self._preview_entry = entry
        self._preview_raw = None
        self._preview_generation += 1
        self.query_one("#content").display = False
        self.query_one("#empty").display = False
        preview = self.query_one("#preview", RichLog)
        preview.display = True
        preview.clear()
        preview.write(Text("Loading diff…", style=MUTED))
        preview.focus()
        title = gitdata.display_path(entry.change.path if entry.change else entry.commit.subject)
        self.query_one("#subtitle", Static).update(Text("← " + title, style=NEUTRAL))
        self._update_controls()
        self.load_preview(entry, self.current_snapshot, self.committed, self._preview_generation)

    @on(Button.Pressed, "#format-preview")
    def toggle_preview_format(self) -> None:
        self.formatted_preview = not self.formatted_preview
        self.query_one("#format-preview", Button).label = "Formatted" if self.formatted_preview else "Original"
        if self._preview_entry and self.current_snapshot:
            self._preview_generation += 1
            self.load_preview(self._preview_entry, self.current_snapshot, self.committed, self._preview_generation)

    @work(group="preview", exclusive=True, exit_on_error=False)
    async def load_preview(self, entry: Entry, snap: Any, committed: bool, generation: int) -> None:
        try:
            if entry.change:
                change = entry.change
                raw = await asyncio.to_thread(
                    gitdata.file_diff, snap.root, snap.base_sha, change.path,
                    committed=committed, head=snap.head, old_path=change.old_path,
                )
                if self.formatted_preview:
                    from .formatting import formatted_diff
                    try:
                        raw = await asyncio.to_thread(formatted_diff, snap.root, snap.base_sha, change.path,
                            committed=committed, head=snap.head, old_path=change.old_path)
                        raw = "Formatted preview · display only; line numbers reflect formatting.\n" + raw
                    except Exception:
                        raw = "Original preview · formatting unavailable for this file.\n" + raw
            else:
                raw = await asyncio.to_thread(gitdata.commit_diff, snap.root, entry.commit.sha)
            error = None
        except Exception as exc:
            raw = ""
            error = Text("Unable to read diff: " + gitdata.display_path(str(exc)), style=RED)
        if self._preview_open and generation == self._preview_generation:
            preview = self.query_one("#preview", RichLog)
            self._preview_raw = raw
            self._preview_path = entry.change.path if entry.change else None
            self.render_preview()
            if error:
                preview.clear(); preview.write(error)
            preview.scroll_home(animate=False)

    @on(Button.Pressed, "#split-preview")
    def toggle_split_preview(self) -> None:
        self.split_preview = not self.split_preview
        self.query_one("#split-preview", Button).label = "Split" if self.split_preview else "Stacked"
        self.render_preview()

    def _is_added_preview(self) -> bool:
        return bool(self._preview_entry and self._preview_entry.change
                    and self._preview_entry.change.status == "A")

    def render_preview(self) -> None:
        if not self._preview_open or self._preview_raw is None:
            return
        from .split_diff import split_diff
        preview = self.query_one("#preview", RichLog)
        scroll = preview.scroll_offset
        raw = self._preview_raw
        self._update_controls()
        rendered = (split_diff(raw, self._preview_path, max(1, preview.size.width-3))
                    if self.split_preview and not self._is_added_preview() else diff_text(raw, self._preview_path))
        preview.clear()
        preview.write(rendered if raw.strip() else Text("No text diff (the file may be binary).", style=MUTED))
        preview.scroll_to(x=scroll.x, y=scroll.y, animate=False)

    def action_back(self) -> None:
        if not self._preview_open:
            return
        self._preview_open = False
        self._preview_entry = None
        self._preview_generation += 1
        self.query_one("#preview").display = False
        self._signature = None
        self._render_snapshot()
        self._update_controls()
        self.query_one("#content", ProjectTree).focus()


def run_panel(kind: str, root: str, base_override: str | None = None) -> None:
    DashboardPanel(kind, root, base_override).run()

"""Keep resource graphs visible below btop's minimum terminal dimensions."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static
from textual import work

from . import processdata


def fits_btop(columns: int, rows: int) -> bool:
    return columns >= 60 and rows >= 18


class History(Static):
    def __init__(self, color: str, **kwargs):
        super().__init__(**kwargs)
        self.samples: deque[float] = deque(maxlen=300)
        self.graph_color = color

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        samples = list(self.samples)[-width:] if width else []
        values = [0.0] * (width - len(samples)) + samples
        result = Text()
        for row in range(height):
            for value in values:
                eighths = round(max(0, min(100, value)) / 100 * height * 8)
                fill = max(0, min(8, eighths - (height - row - 1) * 8))
                result.append(" ▁▂▃▄▅▆▇█"[fill], style=self.graph_color)
            if row < height - 1:
                result.append("\n")
        return result

    def add(self, value: float | None) -> None:
        if value is not None:
            self.samples.append(value)
            self.refresh()


def gib(value: int | None) -> str:
    return "—" if value is None else f"{value / 1024**3:.1f} GiB"


def memory_readings(raw: str | None) -> dict[str, int | None]:
    """Use the same active+wired definition as btop 1.4.7 on macOS.

    Reference: https://github.com/aristocratos/btop/blob/v1.4.7/src/osx/btop_collect.cpp
    """
    result: dict[str, int | None] = dict(used=None, cached=None, free=None)
    match = re.search(r"page size of (\d+) bytes", raw or "")
    if not match:
        return result
    page_size = int(match.group(1))
    pages = {name: int(count) for name, count in
             re.findall(r"^([^:\n]+):\s+(\d+)\.?$", raw or "", re.MULTILINE)}
    if all(name in pages for name in ("Pages active", "Pages wired down")):
        result["used"] = (pages["Pages active"] + pages["Pages wired down"]) * page_size
    for label, name in (("cached", "File-backed pages"), ("free", "Pages free")):
        if name in pages:
            result[label] = pages[name] * page_size
    return result


class CompactResources(App[None]):
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: #000000; color: #b8b8b8; }
    .box { height: 1fr; min-height: 5; border: round #454545; padding: 0 1;
        border-title-color: #d0d0d0; background: #000000; }
    .reading { height: auto; min-height: 1; color: #b8b8b8; }
    .muted { color: #777777; }
    #cpu { color: #6fa7ff; }
    #memory { color: #79d99b; }
    History { height: 1fr; min-height: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="box", id="cpu-box"):
            yield Static("CPU  sampling…", classes="reading", id="cpu")
            yield History("#6fa7ff", id="cpu-history")
            yield Static("", classes="reading muted", id="load")
        with Vertical(classes="box", id="memory-box"):
            yield Static("Memory  sampling…", classes="reading", id="memory")
            yield History("#79d99b", id="memory-history")
            yield Static("", classes="reading muted", id="available")
            yield Static("", classes="reading muted", id="cached-free")

    def on_mount(self) -> None:
        self.query_one("#cpu-box").border_title = "CPU"
        self.query_one("#memory-box").border_title = "MEMORY"
        self.refresh_readings()
        self.set_interval(2, self.refresh_readings)

    @work(exclusive=True, exit_on_error=False)
    async def refresh_readings(self) -> None:
        def sample():
            cpu = processdata._system_cpu(processdata._cpu_ticks())
            total = processdata._memory_total()
            readings = memory_readings(processdata._run("/usr/bin/vm_stat"))
            return cpu, total, readings

        cpu, total, readings = await asyncio.to_thread(sample)
        used = readings["used"]
        self.query_one("#cpu", Static).update("CPU  sampling…" if cpu is None else f"CPU  {cpu:.1f}%")
        self.query_one("#cpu-history", History).add(cpu)
        self.query_one("#cpu-box").border_subtitle = datetime.now().strftime("%I:%M %p")
        try:
            loads = " / ".join(f"{value:.2f}" for value in os.getloadavg())
            self.query_one("#load", Static).update(f"{os.cpu_count()} cores · Load {loads}")
        except OSError:
            pass
        self.query_one("#memory", Static).update(f"Used {gib(used)} / {gib(total)}")
        self.query_one("#memory-history", History).add(used / total * 100 if used is not None and total else None)
        available = total - used if total is not None and used is not None else None
        self.query_one("#available", Static).update(f"Available {gib(available)}")
        self.query_one("#cached-free", Static).update(f"Cached {gib(readings['cached'])} · Free {gib(readings['free'])}")


def run(config: Path, themes: Path) -> None:
    """Switch only the upper pane's renderer as its actual dimensions change."""
    while True:
        full = fits_btop(*shutil.get_terminal_size((68, 28)))
        command = ([shutil.which("btop") or "btop", "--config", str(config),
                    "--themes-dir", str(themes), "--force-utf"] if full else
                   [sys.executable, "-m", "dashboard.resource_graphs", "--compact"])
        child = subprocess.Popen(command)
        resized = False
        try:
            while child.poll() is None:
                try:
                    child.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    if fits_btop(*shutil.get_terminal_size((68, 28))) != full:
                        resized = True
                        break
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        if not resized:
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--btop-config", type=Path)
    parser.add_argument("--themes-dir", type=Path)
    options = parser.parse_args()
    if options.compact:
        CompactResources().run()
    else:
        if options.btop_config is None or options.themes_dir is None:
            parser.error("btop configuration and theme directory are required")
        run(options.btop_config, options.themes_dir)


if __name__ == "__main__":
    main()

"""Keep btop's CPU/memory graphs above the dashboard's process view.

The stack lives inside the existing right-hand terminal. Its private tmux
server keeps the outer dashboard's pane identities and coding session intact.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


THEME = '''theme[main_bg]="#000000"
theme[main_fg]="#b8b8b8"
theme[title]="#d0d0d0"
theme[hi_fg]="#6fa7ff"
theme[selected_bg]="#182330"
theme[selected_fg]="#ffffff"
theme[inactive_fg]="#666666"
theme[cpu_box]="#454545"
theme[mem_box]="#454545"
theme[cpu_start]="#4e79b2"
theme[cpu_mid]="#6fa7ff"
theme[cpu_end]="#c3daff"
theme[used_start]="#468463"
theme[used_mid]="#79d99b"
theme[used_end]="#c3f2d3"
'''

BTOP_CONFIG = '''color_theme = "dashboard-black"
theme_background = true
truecolor = true
force_tty = false
shown_boxes = "cpu mem"
disable_presets = "All"
update_ms = 2000
cpu_bottom = false
cpu_single_graph = true
show_cpu_freq = true
check_temp = true
show_uptime = true
show_disks = false
mem_graphs = true
show_swap = true
clock_format = "%I:%M %p"
background_update = true
terminal_sync = true
'''

TMUX_CONFIG = '''set -g default-terminal tmux-256color
set -as terminal-features ',tmux-256color:RGB:extkeys:focus:sync'
set -s escape-time 10
set -g mouse on
set -g status off
set -g prefix None
set -g prefix2 None
set -g default-shell /bin/zsh
set -g window-size latest
set -g automatic-rename off
set -g allow-rename off
set -g pane-border-status off
set -g pane-border-style 'bg=#000000,fg=#383838'
set -g pane-active-border-style 'bg=#000000,fg=#383838'
set -g window-style 'bg=#000000,fg=#b8b8b8'
set -g window-active-style 'bg=#000000,fg=#b8b8b8'
set -g remain-on-exit on
unbind-key -aq -T prefix
unbind-key -aq -T root
bind-key -n MouseDown1Pane { select-pane -t = ; send-keys -M }
bind-key -n MouseDrag1Pane send-keys -M
bind-key -n MouseUp1Pane send-keys -M
bind-key -n MouseDrag1Border resize-pane -M
bind-key -n WheelUpPane send-keys -M
bind-key -n WheelDownPane send-keys -M
'''


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env.pop("NO_COLOR", None)
    env["COLORTERM"] = "truecolor"
    return env


def tmux(socket: str, *args: str, check: bool = True) -> str:
    result = subprocess.run([shutil.which("tmux") or "tmux", "-L", socket, *map(str, args)],
                            env=_environment(), capture_output=True, text=True, timeout=10)
    if result.returncode and check:
        raise RuntimeError(result.stderr.strip() or "Unable to open the resource panels.")
    return result.stdout.strip()


def prepare(directory: Path, socket: str, session: str, width: int, height: int,
            *, restart: bool = True, initial_view: str = "cpu") -> str:
    """Prepare just the two read-only resource panes; never touch outer panes."""
    if initial_view not in ("cpu", "gpu", "memory"):
        raise ValueError("Initial process view must be cpu, gpu, or memory.")
    binary = shutil.which("btop")
    if not binary:
        raise RuntimeError("CPU and memory graphs require btop: brew install btop")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dashboard-black.theme").write_text(THEME)
    (directory / "btop.conf").write_text(BTOP_CONFIG)
    (directory / "tmux.conf").write_text(TMUX_CONFIG)
    upper_command = shlex.join([sys.executable, "-m", "dashboard.resource_graphs",
                               "--btop-config", str(directory / "btop.conf"),
                               "--themes-dir", str(directory)])
    lower_command = shlex.join([sys.executable, "-m", "dashboard.resource_stack",
                                "--process-table", "--view", initial_view])
    exists = bool(tmux(socket, "list-sessions", "-F", "#{session_name}", check=False))
    sessions = tmux(socket, "list-sessions", "-F", "#{session_name}", check=False).splitlines() if exists else []
    if session not in sessions:
        upper = tmux(socket, "-f", str(directory / "tmux.conf"), "new-session", "-d", "-s", session,
                     "-x", str(max(36, width)), "-y", str(max(24, height)), "-P", "-F", "#{pane_id}", upper_command)
        try:
            lower = tmux(socket, "split-window", "-d", "-v", "-t", upper, "-l", "48%",
                         "-P", "-F", "#{pane_id}", lower_command)
            tmux(socket, "set-option", "-t", session, "@resource_btop", upper)
            tmux(socket, "set-option", "-t", session, "@resource_processes", lower)
            tmux(socket, "select-pane", "-t", upper, "-T", "CPU AND MEMORY")
            tmux(socket, "select-pane", "-t", lower, "-T", "PROCESSES")
            tmux(socket, "select-pane", "-t", lower)
        except Exception:
            tmux(socket, "kill-session", "-t", session, check=False)
            raise
    elif restart:
        for role, command in (("btop", upper_command), ("processes", lower_command)):
            pane = tmux(socket, "show-options", "-qv", "-t", session, "@resource_" + role)
            if pane:
                tmux(socket, "respawn-pane", "-k", "-t", pane, command)
        tmux(socket, "source-file", str(directory / "tmux.conf"))
    return session


def run_table(initial_view: str = "cpu") -> None:
    from .processes import ProcessMonitor

    class ProcessTable(ProcessMonitor):
        def on_mount(self) -> None:
            super().on_mount()
            # The real btop graphs above replace these small duplicate meters.
            # Keep the widgets mounted so future GPU/data updates are untouched.
            for summary in self.query(".summary"):
                summary.display = False

    ProcessTable(initial_view=initial_view).run()


def run(initial_view: str = "cpu") -> None:
    from .cli import STATE
    outer = os.environ.get("TMUX", "").split(",")[0] + ":" + os.environ.get("TMUX_PANE", "standalone")
    identity = hashlib.sha256(outer.encode()).hexdigest()[:16]
    socket = os.environ.get("DASHBOARD_RESOURCE_SOCKET", "ghostty-dashboard-resources")
    session = "resources-" + identity
    size = shutil.get_terminal_size((68, 55))
    prepare(STATE / "resources" / identity, socket, session, size.columns, size.lines,
            initial_view=initial_view)
    os.execvpe("tmux", ["tmux", "-L", socket, "attach-session", "-t", session], _environment())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show resource graphs and the process table.")
    parser.add_argument("--process-table", action="store_true")
    parser.add_argument("--view", choices=("cpu", "gpu", "memory"), default="cpu")
    options = parser.parse_args(argv)
    if options.process_table:
        run_table(initial_view=options.view)
    else:
        run(initial_view=options.view)


if __name__ == "__main__":
    main()

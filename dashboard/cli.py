"""Launch a fixed, resizable workspace in the terminal that calls dashboard."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

SOCKET = os.environ.get("DASHBOARD_SOCKET", "ghostty-dashboard")
STATE = Path(os.environ.get("DASHBOARD_STATE_DIR", str(Path.home() / ".local/state/ghostty-dashboard")))

HELP_TEXT = """DASHBOARD CONTROLS

Coding terminal
  Click Hide coding terminal / Show coding terminal, or press F2.
  Hiding keeps your shell and its running programs alive.
  Run grok, opencode, or another command in the coding terminal.
  In native Ghostty mode, click that column and use Cmd + / - to zoom.

Project views
  Click the bottom controls to open a diff, go back, or refresh.
  All changes / Committed changes the file map and its + / - totals.
  Click a folder arrow to expand or collapse it.

Processes
  CPU / GPU / Memory changes the process view.
  Click Filter to find a process; click the arrows to move the selection.

Layout
  Drag borders to resize. The two left sections stay in their positions.
  Leave returns this Ghostty window to your original terminal.
  Your coding programs keep running in the background.
  Run dashboard in your project to return.

Notes / Commits: switch the upper-left section with the button or F3. Notes save automatically.
Native mode: column widths are remembered to cell precision; font zoom still resets.
"""


def status_left() -> str:
    action = "#{?#{==:#{@dashboard_hidden},1},Show,Hide}"
    coding = "#{?#{e|>=:#{client_width},64}," + action + " coding terminal,#{?#{e|>=:#{client_width},31}," + action + " coding,Code}}"
    notes = "#{?#{==:#{@dashboard_top_mode},notes},Commits,Notes}"
    return ("#[range=user|terminal,bold,fg=#8db8ff,bg=#182330] " + coding + " #[norange,default] "
            + "#[range=user|notes,fg=#cccccc,bg=#222222] " + notes + " #[norange,default]")


def status_right() -> str:
    # Budget the whole row: compact labels fit alongside Leave at 24 columns.
    return ("#[range=user|help,fg=#cccccc,bg=#222222]#{?#{e|>=:#{client_width},64}, Help ,}#[norange,default] "
            "#[range=user|leave,fg=#cccccc,bg=#222222]#{?#{e|>=:#{client_width},24}, Leave ,}#[norange,default]")


def tmux(*args: str, check: bool = True) -> str:
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env["COLORTERM"] = "truecolor"
    result = subprocess.run(["tmux", "-L", SOCKET, *map(str, args)], env=env,
                            text=True, capture_output=True, timeout=15)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "The dashboard terminal layout could not be opened.")
    return result.stdout.strip()


def command(*args: str) -> str:
    return shlex.join([sys.executable, "-m", "dashboard.cli", *args])


def tmux_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def config_text() -> str:
    environment = shlex.join(["env", f"DASHBOARD_SOCKET={SOCKET}", f"DASHBOARD_STATE_DIR={STATE}"])
    toggle = environment + " " + command("_toggle", "--session", "#{session_id}")
    notes_toggle = environment + " " + command("_toggle_notes", "--session", "#{session_id}")
    leave = environment + " " + command("_leave", "--session", "#{session_id}", "--client", "#{client_name}")
    click = environment + " " + command("_status_click", "--session", "#{session_id}", "--control", "#{mouse_status_range}", "--client", "#{client_name}")
    help_text = "Drag borders to resize | F2 or Ctrl-b t: coding terminal | Ctrl-b arrows: focus | Ctrl-b Ctrl-arrows: resize | Ctrl-b d: leave (keeps work running)"
    return "\n".join([
        "# Dashboard-only server. Does not read or alter your normal tmux configuration.",
        "set -g default-terminal 'tmux-256color'",
        "set -as terminal-features ',xterm-ghostty:RGB:extkeys:clipboard:focus:sync'",
        "set -as terminal-features ',xterm-256color:RGB'",
        "set -s extended-keys on",
        "set -s extended-keys-format csi-u",
        "set -s escape-time 10",
        "set -g focus-events on",
        "set -g mouse on",
        "set -g set-clipboard on",
        "set -g history-limit 50000",
        "set -g default-shell /bin/zsh",
        "set -g default-command '/bin/zsh -l'",
        "set -g automatic-rename off",
        "set -g allow-rename off",
        "set -g renumber-windows off",
        "set -g detach-on-destroy on",
        "set -g remain-on-exit on",
        "set -g window-size latest",
        "set -g pane-border-status top",
        "set -g pane-border-lines single",
        "set -g window-style 'bg=#000000,fg=#d0d0d0'",
        "set -g window-active-style 'bg=#000000,fg=#d0d0d0'",
        "set -g pane-border-style 'bg=#000000,fg=#383838'",
        "set -g pane-active-border-style 'bg=#000000,fg=#6fa7ff'",
        "set -g pane-border-format ' #{pane_title} '",
        "set -g status-position bottom",
        "set -g status-style 'bg=#000000,fg=#b6b6b6'",
        "set -g status-left-length 100",
        "set -g status-left " + tmux_quote(status_left()),
        "set -g status-right-length 100",
        "set -g status-right " + tmux_quote(status_right()),
        "set -g status-format[0] '#{E:status-left}#[align=right]#{E:status-right}'",
        "set -g message-style 'bg=#191919,fg=#eeeeee'",
        "unbind-key -a -T prefix",
        "bind-key C-b send-prefix",
        "bind-key d run-shell -b " + tmux_quote(leave),
        "bind-key r refresh-client",
        "bind-key Up select-pane -U",
        "bind-key Down select-pane -D",
        "bind-key Left select-pane -L",
        "bind-key Right select-pane -R",
        "bind-key -r C-Up resize-pane -U 2",
        "bind-key -r C-Down resize-pane -D 2",
        "bind-key -r C-Left resize-pane -L 2",
        "bind-key -r C-Right resize-pane -R 2",
        "bind-key '[' copy-mode",
        "bind-key ']' paste-buffer",
        f"bind-key '?' display-message {tmux_quote(help_text)}",
        f"bind-key t run-shell -b {tmux_quote(toggle)}",
        f"bind-key -n F2 run-shell -b {tmux_quote(toggle)}",
        f"bind-key -n F3 run-shell -b {tmux_quote(notes_toggle)}",
        # Keep resize-by-drag; remove every default context menu offering pane moves.
        "bind-key -n MouseDrag1Border resize-pane -M",
        "bind-key -n MouseDown3Pane select-pane -t =",
        "bind-key -n MouseDown3Border select-pane -t =",
        "unbind-key -n MouseDown3Status",
        "unbind-key -n MouseDown3StatusLeft",
        "unbind-key -n MouseDown3StatusRight",
        "unbind-key -n M-MouseDown3Pane",
        "unbind-key -n M-MouseDown3Status",
        "unbind-key -n M-MouseDown3StatusLeft",
        "unbind-key -n M-MouseDown3StatusRight",
        "unbind-key -q -n C-MouseDown1Pane",
        "unbind-key -q -n C-MouseDown1Status",
        "if-shell -F '#{>=:#{version},3.7}' 'unbind-key -q -n MouseDown1Control8'",
        "if-shell -F '#{>=:#{version},3.7}' 'unbind-key -q -n MouseDown1Control9'",
        "unbind-key -n WheelUpStatus",
        "unbind-key -n WheelDownStatus",
        "bind-key -n MouseDown1Status run-shell -b " + tmux_quote(click),
        "bind-key -n MouseDown1StatusLeft run-shell -b " + tmux_quote(click),
        "bind-key -n MouseDown1StatusRight run-shell -b " + tmux_quote(click),
        "",
    ])


def ensure_state() -> Path:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = STATE / "tmux.conf"
    content = config_text()
    if not config.exists() or config.read_text() != content:
        config.write_text(content)
    return config


@contextlib.contextmanager
def locked(name: str):
    ensure_state()
    path = STATE / (hashlib.sha256(name.encode()).hexdigest()[:16] + ".lock")
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def option(session: str, key: str) -> str:
    return tmux("show-options", "-qv", "-t", session, "@dashboard_" + key)


def set_option(session: str, key: str, value: str) -> None:
    tmux("set-option", "-t", session, "@dashboard_" + key, value)
    if key in ("hidden", "notes_hidden", "top_mode"):
        # Grouped views share windows, but session options need synchronizing.
        for role in ("left", "terminal", "monitor", "notes", "top"):
            view = option(session, "view_" + role)
            if view:
                tmux("set-option", "-t", view, "@dashboard_" + key, value)


def status_click(session: str, control: str, client: str) -> None:
    """Dispatch only the button under the mouse; blank status space is inert."""
    if control == "terminal":
        toggle_terminal(session)
    elif control == "notes":
        toggle_notes(session)
    elif control == "help":
        tmux("display-popup", "-c", client, "-E", "-w", "80%", "-h", "70%",
             "-T", "Dashboard controls", command("_help"))
    elif control == "leave":
        leave_dashboard(session, client)


def leave_dashboard(session: str, client: str) -> None:
    """Use the same return-to-shell path for Leave and the detach shortcut."""
    parent = option(session, "parent") or session
    if option(parent, "backend") == "native":
        from .native_workspace import leave_workspace
        leave_workspace(parent)
    else:
        tmux("detach-client", "-t", client)


@contextlib.contextmanager
def marked_origin():
    """Identify the invoking Ghostty surface using only its own foreground TTY.

    Ghostty exposes no TTY-to-surface API. A temporary, random title provides
    an exact match even if focus changes while the launcher starts. Ghostty 1.3
    does not implement title push/pop, so the directory title replaces the
    marker afterward; the shell can update it normally on its next prompt.
    """
    if os.environ.get("TERM_PROGRAM", "").lower() != "ghostty":
        raise RuntimeError("Run dashboard from a normal Ghostty terminal, or use dashboard --tmux.")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Run dashboard in an interactive Ghostty terminal.")
    input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
    tty = os.ttyname(input_fd)
    if tty != os.ttyname(output_fd) or os.tcgetpgrp(input_fd) != os.getpgrp():
        raise RuntimeError("Run dashboard in the foreground of your Ghostty terminal.")
    marker = "dashboard-origin-" + uuid.uuid4().hex
    title = Path.cwd().name or "Terminal"
    title = "".join(char for char in title if ord(char) >= 32 and ord(char) != 127)
    sys.stdout.write("\x1b]2;" + marker + "\x07")
    sys.stdout.flush()
    try:
        yield marker, tty
    finally:
        sys.stdout.write("\x1b]2;" + title + "\x07")
        sys.stdout.flush()


def run_native_workspace(session: str, *, inside_tmux: bool = False) -> int:
    from . import native, native_workspace
    if inside_tmux:
        # Re-entering from Grok's shell may focus an existing dashboard, but
        # must never turn its coding surface into a second left-hand client.
        state = native_workspace.read_state(session)
        if not state or option(session, "backend") != "native" or not native.focus_if_alive(state):
            raise RuntimeError("Run dashboard from a normal Ghostty terminal to open its layout.")
        from .top_section import ensure_native
        ensure_native(session)
        return 0
    with marked_origin() as (marker, tty):
        state = native_workspace.open_workspace(session, origin_marker=marker, origin_tty=tty)
        from .top_section import ensure_native
        ensure_native(session)
    if state and state.get("launch_mode") == "inplace":
        try:
            return native_workspace.attach_origin(session, state["generation"])
        finally:
            # Returning from tmux restores the pre-dashboard screen. Give the
            # original shell a clean terminal for its next prompt.
            sys.stdout.write("\x1b[0m\x1b[2J\x1b[H")
            sys.stdout.flush()
    return 0


def session_name(root: str) -> str:
    root = str(Path(root).resolve())
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", Path(root).name)[:24] or "project"
    return "dash-" + name + "-" + hashlib.sha256(root.encode()).hexdigest()[:10]


def create_workspace(root: str, base: str | None, width: int, height: int) -> str:
    name = session_name(root)
    config = ensure_state()
    with locked(name):
        exists = subprocess.run(["tmux", "-L", SOCKET, "has-session", "-t", name],
                                capture_output=True).returncode == 0
        if exists:
            if (base or "") != option(name, "base") and base is not None:
                raise RuntimeError("This project's dashboard is already open with another comparison branch.")
            return name
        panel_args = ["--root", root] + (["--base", base] if base else [])
        left_cmd = command("_panel", "commits", *panel_args)
        # Start in the caller's size, then let tmux follow actual terminal resizes.
        output = tmux("-f", str(config), "new-session", "-d", "-s", name, "-n", "dashboard",
                      "-c", root, "-x", str(width), "-y", str(height),
                      "-e", "COLORTERM=truecolor", "-P", "-F", "#{pane_id}", left_cmd)
        left = output.splitlines()[-1]
        try:
            center = tmux("split-window", "-d", "-h", "-t", left, "-l", "67%", "-c", root,
                          "-P", "-F", "#{pane_id}", "/bin/zsh -l")
            right = tmux("split-window", "-d", "-h", "-t", center, "-l", "50%", "-c", root,
                         "-P", "-F", "#{pane_id}", command("_monitor"))
            files = tmux("split-window", "-d", "-v", "-t", left, "-l", "60%", "-c", root,
                         "-P", "-F", "#{pane_id}", command("_panel", "files", *panel_args))
            for role, pane, title in (("commits", left, "COMMITS"), ("files", files, "CHANGES"),
                                      ("terminal", center, "CODING TERMINAL"), ("monitor", right, "PROCESSES")):
                set_option(name, role, pane)
                tmux("select-pane", "-t", pane, "-T", title)
            set_option(name, "root", root)
            set_option(name, "base", base or "")
            set_option(name, "hidden", "0")
            set_option(name, "window", tmux("display-message", "-p", "-t", left, "#{window_id}"))
            tmux("set-environment", "-t", name, "DASHBOARD_PROJECT", root)
            tmux("select-pane", "-t", center)
        except Exception:
            # This session was created by this invocation; preserve all existing sessions.
            tmux("kill-session", "-t", name, check=False)
            raise
    return name


def toggle_notes(session):
    from .top_section import toggle
    toggle(session)


def toggle_terminal(session: str) -> None:
    session = tmux("display-message", "-p", "-t", session, "#{session_id}")
    session = option(session, "parent") or session
    if option(session, "backend") == "native":
        from .native_workspace import toggle_center
        toggle_center(session)
        return
    with locked(session + "-toggle"):
        pane = option(session, "terminal")
        right = option(session, "monitor")
        window = option(session, "window")
        root = option(session, "root")
        if not pane or not right or not window:
            raise RuntimeError("This session is not a dashboard workspace.")
        hidden = option(session, "hidden") == "1"
        dead = tmux("display-message", "-p", "-t", pane, "#{pane_dead}") == "1"
        if dead:
            tmux("respawn-pane", "-t", pane, "-c", root, "/bin/zsh -l")
            if not hidden:
                tmux("select-pane", "-t", pane)
                return
        if not hidden:
            layout = tmux("display-message", "-p", "-t", window, "#{window_layout}")
            size = tmux("display-message", "-p", "-t", pane, "#{pane_width},#{pane_height}")
            set_option(session, "layout", layout)
            set_option(session, "terminal_size", size)
            tmux("break-pane", "-d", "-s", pane, "-t", session + ":90", "-n", "terminal-hidden")
            w, h = size.split(",")
            tmux("resize-window", "-t", pane, "-x", w, "-y", str(int(h) + 2))
            tmux("select-window", "-t", window)
            width = int(tmux("display-message", "-p", "-t", window, "#{window_width}"))
            tmux("resize-pane", "-t", option(session, "files"), "-x", str(width // 2))
            tmux("select-pane", "-t", option(session, "files"))
            set_option(session, "hidden", "1")
        else:
            w = option(session, "terminal_size").split(",")[0] or "60"
            right_width = int(tmux("display-message", "-p", "-t", right, "#{pane_width}"))
            tmux("join-pane", "-d", "-h", "-b", "-s", pane, "-t", right,
                 "-l", str(max(10, min(int(w), right_width - 10))))
            layout = option(session, "layout")
            if layout:
                tmux("select-layout", "-t", window, layout)
            # Some tmux versions assign saved layout cells in pane-list order
            # after join-pane. Restore role positions without restarting either PID.
            terminal_left = int(tmux("display-message", "-p", "-t", pane, "#{pane_left}"))
            monitor_left = int(tmux("display-message", "-p", "-t", right, "#{pane_left}"))
            if terminal_left > monitor_left:
                tmux("swap-pane", "-d", "-s", pane, "-t", right)
            tmux("select-window", "-t", window)
            tmux("select-pane", "-t", pane)
            set_option(session, "hidden", "0")
        tmux("refresh-client", "-S", check=False)


def run_monitor(initial_view: str = "cpu") -> None:
    """Keep btop CPU/memory above the process view in the fixed right column."""
    from .resource_stack import run
    run(initial_view=initial_view)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args and args[0] == "_panel":
            parser = argparse.ArgumentParser()
            parser.add_argument("kind", choices=["commits", "files"])
            parser.add_argument("--root", required=True)
            parser.add_argument("--base")
            opts = parser.parse_args(args[1:])
            os.environ.pop("NO_COLOR", None)
            os.environ["COLORTERM"] = "truecolor"
            from .panels import run_panel
            run_panel(opts.kind, opts.root, opts.base)
            return 0
        if args and args[0] == "_monitor":
            parser = argparse.ArgumentParser()
            parser.add_argument("--view", choices=["cpu", "gpu", "memory"], default="cpu")
            run_monitor(parser.parse_args(args[1:]).view)
            return 0
        if args and args[0] == "_notes":
            parser = argparse.ArgumentParser()
            parser.add_argument("--root", required=True)
            from .notes import NotesApp
            NotesApp(parser.parse_args(args[1:]).root).run()
            return 0
        if args and args[0] == "_toggle_notes":
            parser = argparse.ArgumentParser()
            parser.add_argument("--session", required=True)
            toggle_notes(parser.parse_args(args[1:]).session)
            return 0
        if args and args[0] == "_toggle":
            parser = argparse.ArgumentParser()
            parser.add_argument("--session", required=True)
            toggle_terminal(parser.parse_args(args[1:]).session)
            return 0
        if args and args[0] == "_leave":
            parser = argparse.ArgumentParser()
            parser.add_argument("--session", required=True)
            parser.add_argument("--client", required=True)
            opts = parser.parse_args(args[1:])
            leave_dashboard(opts.session, opts.client)
            return 0
        if args and args[0] == "_status_click":
            parser = argparse.ArgumentParser()
            parser.add_argument("--session", required=True)
            parser.add_argument("--control", required=True)
            parser.add_argument("--client", required=True)
            opts = parser.parse_args(args[1:])
            status_click(opts.session, opts.control, opts.client)
            return 0
        if args and args[0] == "_help":
            print(HELP_TEXT)
            try:
                input("Press Enter to return.")
            except (EOFError, KeyboardInterrupt):
                pass
            return 0

        parser = argparse.ArgumentParser(prog="dashboard", description="Open your project's fixed dashboard inside Ghostty.",
            epilog="Drag borders to resize. F2 toggles the center terminal without stopping it. Ctrl-b d leaves; dashboard returns.")
        parser.add_argument("path", nargs="?", default=".", help="Project folder or a file within it (default: current folder)")
        parser.add_argument("--base", help="Comparison branch (automatically detects main / master)")
        parser.add_argument("--terminal", action="store_true", help="Show or hide this project's coding terminal")
        parser.add_argument("--tmux", action="store_true", help="Use one terminal surface (shared text size)")
        parser.add_argument("--version", action="version", version="dashboard 1.3.0")
        parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--size", default=None, help=argparse.SUPPRESS)
        opts = parser.parse_args(args)
        for executable in ("tmux", "git"):
            if not shutil.which(executable):
                raise RuntimeError(f"Missing {executable}. Install it first with: brew install {executable}")
        from .gitdata import discover_root
        root = discover_root(str(Path(opts.path).expanduser().resolve()))
        if opts.size:
            width, height = map(int, opts.size.lower().split("x"))
        else:
            size = shutil.get_terminal_size((180, 48))
            width, height = size.columns, size.lines
        # Refuse nested tmux before changing any session.
        active_tmux = os.environ.get("TMUX", "")
        if not opts.background and not opts.terminal and not active_tmux and (not sys.stdin.isatty() or not sys.stdout.isatty()):
            raise RuntimeError("Run dashboard in an interactive terminal.")
        if active_tmux:
            own_socket = tmux("display-message", "-p", "#{socket_path}", check=False)
            if active_tmux.split(",")[0] != own_socket:
                raise RuntimeError("Open dashboard from a regular Ghostty terminal, outside an existing tmux session.")
            if not opts.size:
                target = os.environ.get("TMUX_PANE")
                target_args = ["-t", target] if target else []
                dimensions = tmux("display-message", "-p", *target_args, "#{window_width},#{window_height}")
                width, height = map(int, dimensions.split(","))
        native_launch = not opts.tmux and not opts.background and not opts.terminal and sys.platform == "darwin"
        if native_launch:
            # Native mode reuses the caller's window, which may start narrow.
            # These are only staging dimensions until real surfaces attach.
            width, height = max(180, width), max(48, height)
        elif width < 90 or height < 24:
            existing = subprocess.run(["tmux", "-L", SOCKET, "has-session", "-t", session_name(root)], capture_output=True)
            if existing.returncode != 0:
                raise RuntimeError("Enlarge the terminal to at least 90 columns × 24 rows before opening dashboard.")
        name = create_workspace(root, opts.base, width, height)
        if opts.terminal:
            toggle_terminal(name)
            return 0
        if opts.background:
            print(json.dumps({"session": name, "socket": SOCKET, "root": root}))
            return 0
        from .native_workspace import restore_tmux
        if native_launch:
            return run_native_workspace(name, inside_tmux=bool(active_tmux))
        if option(name, "backend") == "native":
            restore_tmux(name)
        if active_tmux:
            tmux("switch-client", "-t", name)
            tmux("select-window", "-t", option(name, "window"))
            return 0
        env = os.environ.copy()
        env.pop("NO_COLOR", None)
        env["COLORTERM"] = "truecolor"
        os.execvpe("tmux", ["tmux", "-L", SOCKET, "attach-session", "-t", name], env)
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

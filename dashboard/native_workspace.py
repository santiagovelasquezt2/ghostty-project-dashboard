"""Persistent tmux windows behind independently zoomable Ghostty surfaces.

Preparation and rollback use only the private tmux server. Native UI operations
are isolated in open_workspace/toggle_center and run when the user invokes them.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

from . import cli


ROLES = ("left", "terminal", "monitor")


def controller(session: str) -> str:
    return cli.option(session, "parent") or session


def state_path(session: str) -> Path:
    return cli.STATE / (cli.session_name(cli.option(session, "root")) + "-native.json")


def read_state(session: str) -> dict | None:
    try:
        state = json.loads(state_path(session).read_text())
        return state if isinstance(state, dict) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _persist_state(session: str, state: dict) -> None:
    path = state_path(session)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.chmod(0o600)
    temporary.replace(path)


def write_state(session: str, state: dict) -> None:
    _persist_state(session, state)
    cli.set_option(session, "hidden", "1" if state["hidden"] else "0")


def attach_commands(session: str) -> dict[str, str]:
    return {
        role: shlex.join(["/usr/bin/env", "-u", "TMUX", "-u", "TMUX_PANE", "-u", "NO_COLOR", "COLORTERM=truecolor",
                          shutil.which("tmux") or "tmux", "-L", cli.SOCKET,
                          "attach-session", "-t", cli.option(session, "view_" + role)])
        for role in ROLES
    }


def prepare(session: str) -> None:
    """Move existing panes into three windows without restarting any process."""
    if cli.option(session, "backend") == "native":
        return
    left = cli.option(session, "window")
    center = cli.option(session, "terminal")
    monitor = cli.option(session, "monitor")
    hidden = cli.option(session, "hidden") == "1"
    # A previously hidden terminal already has the complete saved layout.
    if hidden:
        cli.toggle_terminal(session)
    layout = cli.tmux("display-message", "-p", "-t", left, "#{window_layout}")
    cli.set_option(session, "pre_native_layout", layout)
    cli.set_option(session, "pre_native_hidden", "1" if hidden else "0")
    cli.set_option(session, "backend", "preparing")
    try:
        windows = {"left": left}
        for role, pane in (("terminal", center), ("monitor", monitor)):
            cli.tmux("break-pane", "-d", "-s", pane, "-n", "dashboard-" + role)
            windows[role] = cli.tmux("display-message", "-p", "-t", pane, "#{window_id}")
        for role, window in windows.items():
            view = cli.session_name(cli.option(session, "root")) + "-" + role
            cli.tmux("new-session", "-d", "-t", session, "-s", view)
            cli.set_option(session, "view_" + role, view)
            cli.set_option(view, "parent", session)
            cli.set_option(view, "role", role)
            # Grouped sessions share windows, but select them independently.
            cli.tmux("select-window", "-t", view + ":" + window)
            cli.tmux("set-option", "-t", view, "status", "on" if role == "left" else "off")
            cli.tmux("set-option", "-w", "-t", window, "window-size", "latest")
            if role != "left":
                cli.tmux("set-option", "-w", "-t", window, "pane-border-status", "off")
        left_view = cli.option(session, "view_left")
        cli.tmux("set-option", "-t", left_view, "status-right", cli.status_right())
        cli.tmux("set-option", "-t", left_view, "status-left-length", "100")
        cli.tmux("set-option", "-t", left_view, "status-left", cli.status_left())
        cli.tmux("select-pane", "-t", center)
        cli.tmux("select-window", "-t", session + ":" + left)
        cli.set_option(session, "backend", "native")
        cli.set_option(session, "hidden", "0")
    except Exception:
        restore_tmux(session, restore_hidden=True)
        raise


def restore_tmux(session: str, *, restore_hidden: bool = False) -> None:
    """Rejoin existing panes, including after an unsuccessful native launch."""
    if cli.option(session, "backend") not in ("native", "preparing"):
        return
    left = cli.option(session, "window")
    files = cli.option(session, "files")
    monitor = cli.option(session, "monitor")
    center = cli.option(session, "terminal")
    # The controller owns the same windows, so removing its view sessions cannot
    # kill pane processes. Kill only the three sessions recorded by this app.
    for role in ROLES:
        view = cli.option(session, "view_" + role)
        if view:
            cli.tmux("kill-session", "-t", view, check=False)
            cli.set_option(session, "view_" + role, "")
    # select-layout assigns saved geometry in current pane order. Restore the
    # original commits/files/terminal/monitor order before applying that layout;
    # joining both panes beside commits would put the terminal under commits.
    for pane, target in ((center, files), (monitor, center)):
        current = cli.tmux("display-message", "-p", "-t", pane, "#{window_id}")
        if current != left:
            cli.tmux("join-pane", "-d", "-h", "-s", pane, "-t", target)
    cli.tmux("set-option", "-w", "-t", left, "pane-border-status", "top")
    layout = cli.option(session, "pre_native_layout")
    if layout:
        cli.tmux("select-layout", "-t", left, layout)
    cli.set_option(session, "backend", "tmux")
    cli.set_option(session, "hidden", "0")
    cli.tmux("select-window", "-t", session + ":" + left)
    cli.tmux("select-pane", "-t", center)
    state_path(session).unlink(missing_ok=True)
    if restore_hidden and cli.option(session, "pre_native_hidden") == "1":
        cli.toggle_terminal(session)


def _detach_origin(session: str, state: dict) -> None:
    """Detach only the saved foreground client, never another attached viewer."""
    tty = state.get("origin_tty")
    view = cli.option(session, "view_left")
    if state.get("launch_mode") != "inplace" or not tty or not view:
        return
    rows = cli.tmux("list-clients", "-t", view, "-F",
                    "#{session_name}\t#{client_name}\t#{client_tty}", check=False)
    for row in rows.splitlines():
        fields = row.split("\t")
        if len(fields) == 3 and fields[0] == view and fields[2] == tty:
            cli.tmux("detach-client", "-t", fields[1], check=False)


def _warn(message: str, error: Exception) -> None:
    print(f"dashboard: {message}: {error}", file=sys.stderr)


def open_workspace(session: str, *, origin_marker: str | None = None,
                   origin_tty: str | None = None) -> dict | None:
    """Called by the user's dashboard command, never by headless preparation."""
    from . import native
    session = controller(session)
    with cli.locked(session + "-native"):
        previous = read_state(session)
        if (origin_marker is None and previous and cli.option(session, "backend") == "native"
                and native.focus_if_alive(previous)):
            return None
        migrated = cli.option(session, "backend") != "native"
        prepare(session)
        state = None
        root = cli.option(session, "root")
        try:
            commands = attach_commands(session)
            state = (native.launch(root, commands, origin_marker=origin_marker)
                     if origin_marker is not None else native.launch(root, commands))
            state = {**state, "generation": uuid.uuid4().hex, "origin_tty": origin_tty}
            write_state(session, state)
        except Exception:
            # A successful native launch may have borrowed the user's current
            # terminal. Leave preserves it even when persistence then fails.
            if state is not None:
                try:
                    native.leave(state, root)
                except Exception as cleanup_error:
                    _warn("could not close the unfinished dashboard", cleanup_error)
            if migrated:
                restore_tmux(session, restore_hidden=True)
            if previous is not None:
                _persist_state(session, previous)
                if not migrated:
                    cli.set_option(session, "hidden", "1" if previous["hidden"] else "0")
            else:
                state_path(session).unlink(missing_ok=True)
            raise
        if previous is not None:
            try:
                if previous.get("launch_mode") == "inplace":
                    native.leave(previous, root)
                    _detach_origin(session, previous)
                else:
                    native.retire(previous)
            except Exception as retirement_error:
                # The replacement is ready; keep it usable if old UI ownership
                # changed or a previous window was closed manually.
                _warn("the new dashboard opened, but the previous view could not close", retirement_error)
            if previous.get("launch_mode") == "inplace":
                try:
                    if not native.focus_if_alive(state):
                        raise RuntimeError("its native terminals are no longer available")
                except Exception as focus_error:
                    _warn("the new dashboard is ready, but could not receive focus", focus_error)
        return state


def leave_workspace(session: str, expected_generation: str | None = None) -> None:
    """Return the original shell while retaining every backing pane process."""
    from . import native
    session = controller(session)
    with cli.locked(session + "-native"):
        state = read_state(session)
        if state is None or (expected_generation is not None
                             and state.get("generation") != expected_generation):
            return
        native.leave(state, cli.option(session, "root"))
        # Detach wakes attach_origin's finally clause. Clear state first so its
        # generation-guarded cleanup cannot attempt to close native UI twice.
        state_path(session).unlink(missing_ok=True)
        _detach_origin(session, state)


def attach_origin(session: str, generation: str) -> int:
    """Borrow the launching terminal until detach, then clean its side views."""
    session = controller(session)
    with cli.locked(session + "-native"):
        state = read_state(session)
        if (state is None or state.get("generation") != generation
                or state.get("launch_mode") != "inplace"):
            return 0
        command = shlex.split(attach_commands(session)["left"])
    try:
        return subprocess.run(command, check=False).returncode
    finally:
        # Never hold the workspace lock while waiting on this foreground client.
        leave_workspace(session, expected_generation=generation)


def toggle_center(session: str) -> None:
    from . import native
    session = controller(session)
    with cli.locked(session + "-native"):
        state = read_state(session)
        if state is None:
            raise RuntimeError("Run dashboard again to open its Ghostty window.")
        center = cli.option(session, "terminal")
        if cli.tmux("display-message", "-p", "-t", center, "#{pane_dead}") == "1":
            cli.tmux("respawn-pane", "-t", center, "-c", cli.option(session, "root"), "/bin/zsh -l")
            if not state["hidden"]:
                native.focus_if_alive(state)
                return
        updated = {**state, **native.toggle(state, attach_commands(session)["terminal"],
                                          cli.option(session, "root"))}
        write_state(session, updated)

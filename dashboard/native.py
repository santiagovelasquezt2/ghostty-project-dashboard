"""Generate Ghostty 1.3 native layouts; execute them only on an explicit launch.

Each native surface runs a tmux *client*. Closing the center surface must never
kill its tmux session: the caller owns that persistent session. Ghostty's script
API cannot read a surface's current font size or split dimensions, so recreating
the center does not retain its manual font adjustment. geometry.py restores
column proportions using tmux viewport measurements.

API reference: Ghostty.app/Contents/Resources/Ghostty.sdef (Ghostty 1.3.1).
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import subprocess
from typing import Any


_HEADER = "DASHBOARD_NATIVE_V1"
_HEADER_V2 = "DASHBOARD_NATIVE_V2"
_APP = "/Applications/Ghostty.app"
_ROLES = ("left", "terminal", "monitor")


def _text(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be nonempty text")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError(f"{label} contains an unsupported control character")
    return value


def _literal(value: str) -> str:
    """Quote an AppleScript string; shell quoting belongs to the command caller."""
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return '"' + value + '"'


def _root(value: str) -> str:
    value = _text(value, label="Project directory")
    if not Path(value).is_absolute():
        raise ValueError("Project directory must be absolute")
    return value


def _identifier(value: str, *, label: str) -> str:
    value = _text(value, label=label)
    if value == "-" or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} is not a valid Ghostty identifier")
    return value


def _configuration(root: str, command: str) -> str:
    return (
        "{initial working directory:" + _literal(root)
        + ", command:" + _literal(command) + ", wait after command:false}"
    )


def _result(center: str) -> str:
    return (
        f"return {_literal(_HEADER)} & linefeed & (id of dashboardWindow) "
        "& linefeed & (id of dashboardTab) & linefeed & (id of leftTerminal) "
        f"& linefeed & {center} & linefeed & (id of monitorTerminal)"
    )


def build_launch_script(root: str, commands: Mapping[str, str], origin_marker: str | None = None) -> str:
    """Create left / center / monitor native surfaces.

    ``commands`` must contain explicit tmux attach commands for ``left``,
    ``terminal``, and ``monitor``. The left tmux window holds commits and files.
    Without a marker, create a new window. A marker reuses its exact matching
    surface or creates a dashboard tab in that window if other splits exist.
    No command text is typed into an existing terminal; the caller attaches
    its own TTY to the left view for an inplace launch.
    """
    root = _root(root)
    if not isinstance(commands, Mapping):
        raise ValueError("Native commands must be a mapping")
    configs = {
        role: _configuration(root, _text(commands.get(role), label=f"{role} command"))
        for role in _ROLES
    }
    if origin_marker is not None:
        return _build_reuse_script(configs, _identifier(origin_marker, label="Origin marker"))
    return f'''-- Generated dashboard layout. Creating this script does not run it.
tell application {_literal(_APP)}
    set dashboardWindow to missing value
    try
        set dashboardWindow to new window with configuration {configs["left"]}
        set dashboardTab to selected tab of dashboardWindow
        set leftTerminal to terminal 1 of dashboardTab
        set centerTerminal to split leftTerminal direction right with configuration {configs["terminal"]}
        set monitorTerminal to split centerTerminal direction right with configuration {configs["monitor"]}
        if not (perform action "equalize_splits" on centerTerminal) then
            error "Ghostty could not equalize the dashboard columns."
        end if
        activate window dashboardWindow
        select tab dashboardTab
        focus centerTerminal
        {_result("(id of centerTerminal)")}
    on error failureMessage number failureNumber
        if dashboardWindow is not missing value then
            try
                close window dashboardWindow
            end try
        end if
        error failureMessage number failureNumber
    end try
end tell
'''


def _build_reuse_script(configs: Mapping[str, str], marker: str) -> str:
    """Identify the invoking TTY by its unique title, then reuse its window."""
    return f'''-- Match the invoking terminal by its temporary title, never by focus.
tell application {_literal(_APP)}
    set originWindow to missing value
    set originTab to missing value
    set originTerminal to missing value
    repeat 30 times
        set matchCount to 0
        repeat with candidateWindow in every window
            repeat with candidateTab in every tab of candidateWindow
                repeat with candidateTerminal in every terminal of candidateTab
                    if name of candidateTerminal is {_literal(marker)} then
                        set matchCount to matchCount + 1
                        set originWindow to contents of candidateWindow
                        set originTab to contents of candidateTab
                        set originTerminal to contents of candidateTerminal
                    end if
                end repeat
            end repeat
        end repeat
        if matchCount > 1 then error "The invoking Ghostty terminal could not be identified uniquely."
        if matchCount is 1 then exit repeat
        delay 0.05
    end repeat
    if matchCount is not 1 then
        error "Could not identify the invoking Ghostty terminal. Check whether a fixed Ghostty title is configured."
    end if
    set dashboardWindow to originWindow
    set createdTerminalIds to {{}}
    try
        if (count of terminals of originTab) is 1 then
            set launchMode to "inplace"
            set dashboardTab to originTab
            set leftTerminal to originTerminal
        else
            set launchMode to "tab"
            set dashboardTab to new tab in dashboardWindow with configuration {configs["left"]}
            set leftTerminal to terminal 1 of dashboardTab
            set end of createdTerminalIds to id of leftTerminal
        end if
        set centerTerminal to split leftTerminal direction right with configuration {configs["terminal"]}
        set end of createdTerminalIds to id of centerTerminal
        set monitorTerminal to split centerTerminal direction right with configuration {configs["monitor"]}
        set end of createdTerminalIds to id of monitorTerminal
        if not (perform action "equalize_splits" on centerTerminal) then
            error "Ghostty could not equalize the dashboard columns."
        end if
        activate window dashboardWindow
        select tab dashboardTab
        focus centerTerminal
        return {_literal(_HEADER_V2)} & linefeed & (id of dashboardWindow) & linefeed & (id of dashboardTab) & linefeed & (id of leftTerminal) & linefeed & (id of centerTerminal) & linefeed & (id of monitorTerminal) & linefeed & (id of originTab) & linefeed & (id of originTerminal) & linefeed & launchMode
    on error failureMessage number failureNumber
        repeat with createdId in reverse of createdTerminalIds
            try
                if exists terminal id (contents of createdId) then close terminal id (contents of createdId)
            end try
        end repeat
        try
            focus originTerminal
        end try
        error failureMessage number failureNumber
    end try
end tell
'''


def _state_identifiers(state: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    if not isinstance(state, Mapping) or not isinstance(state.get("terminals"), Mapping):
        raise ValueError("Native state must contain terminal identifiers")
    terminals = state["terminals"]
    window = _identifier(state.get("window_id"), label="Window identifier")
    tab = _identifier(state.get("tab_id"), label="Tab identifier")
    left = _identifier(terminals.get("left"), label="Left terminal identifier")
    monitor = _identifier(terminals.get("monitor"), label="Monitor terminal identifier")
    center = terminals.get("terminal", "")
    if center:
        center = _identifier(center, label="Center terminal identifier")
    elif center != "":
        raise ValueError("Hidden center identifier must be empty text")
    active_ids = [left, monitor] + ([center] if center else [])
    if len(set(active_ids)) != len(active_ids):
        raise ValueError("Native terminal identifiers must be distinct")
    return window, tab, left, center, monitor


def _origin_identifiers(state: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """Validate optional V2 provenance, including which surface must survive."""
    if not isinstance(state, Mapping):
        raise ValueError("Native state must contain terminal identifiers")
    if "origin" not in state and "launch_mode" not in state:
        return None
    mode = state.get("launch_mode")
    origin = state.get("origin")
    if mode not in ("inplace", "tab") or not isinstance(origin, Mapping):
        raise ValueError("Native origin must specify an inplace or tab launch")
    origin_tab = _identifier(origin.get("tab_id"), label="Origin tab identifier")
    origin_terminal = _identifier(origin.get("terminal_id"), label="Origin terminal identifier")
    _, tab, left, center, monitor = _state_identifiers(state)
    if mode == "inplace":
        if origin_tab != tab or origin_terminal != left:
            raise ValueError("An inplace dashboard must retain its original left terminal")
    elif origin_tab == tab or origin_terminal in (left, center, monitor):
        raise ValueError("A dashboard tab must be separate from its original terminal")
    return mode, origin_tab, origin_terminal


def _resolve_tab(window: str, tab: str, *, prefix: str = "dashboard") -> str:
    """Locate a stable tab even when AppKit changed its logical window ID.

    The fallback accepts only an exact, unique tab ID. Callers subsequently
    verify the recorded surface IDs belong to that tab before changing it.
    """
    return f'''    set {prefix}Window to missing value
    set {prefix}Tab to missing value
    set {prefix}Ids to {{}}
    if exists window id {_literal(window)} then
        set {prefix}Window to window id {_literal(window)}
        if exists tab id {_literal(tab)} of {prefix}Window then
            set {prefix}Tab to tab id {_literal(tab)} of {prefix}Window
        end if
    end if
    if {prefix}Tab is missing value then
        set matchingTabCount to 0
        repeat with candidateWindow in every window
            repeat with candidateTab in every tab of candidateWindow
                if id of candidateTab is {_literal(tab)} then
                    set matchingTabCount to matchingTabCount + 1
                    set {prefix}Window to contents of candidateWindow
                    set {prefix}Tab to contents of candidateTab
                end if
            end repeat
        end repeat
        if matchingTabCount > 1 then
            error "The recorded Ghostty tab could not be identified uniquely."
        end if
    end if
    if {prefix}Tab is not missing value then
        set {prefix}Ids to id of every terminal of {prefix}Tab
    end if
'''


def _ownership_preflight(state: Mapping[str, Any]) -> str:
    """Check every surviving managed ID before allowing any close operation."""
    window, tab, left, center, monitor = _state_identifiers(state)
    notes = state.get("terminals", {}).get("notes", "")
    if notes:
        notes = _identifier(notes, label="Notes terminal identifier")
        if notes in (left, center, monitor):
            raise ValueError("Notes must have a distinct terminal identifier")
    top = state.get("terminals", {}).get("top", "")
    if top:
        top = _identifier(top, label="Top terminal identifier")
        if top in (left, center, monitor, notes):
            raise ValueError("Top section must have a distinct terminal identifier")
    ids = ", ".join(_literal(value) for value in (left, center, monitor, notes, top) if value)
    return _resolve_tab(window, tab) + f'''    set existingManagedIds to {{}}
    repeat with managedId in {{{ids}}}
        set targetId to contents of managedId
        if exists terminal id targetId then
            if dashboardIds does not contain targetId then
                error "A dashboard terminal was moved to another tab. Move it back before leaving."
            end if
            set end of existingManagedIds to targetId
        end if
    end repeat
'''


def _close_managed(*, retain: str | None = None) -> str:
    exclusion = f" and targetId is not {_literal(retain)}" if retain else ""
    return f'''    repeat with managedId in reverse of existingManagedIds
        set targetId to contents of managedId
        if (exists terminal id targetId){exclusion} then
            close terminal id targetId
        end if
    end repeat
'''


def build_leave_script(state: Mapping[str, Any], root: str) -> str:
    """Return to an original shell, closing only recorded dashboard clients.

    Inplace launch retains its origin surface; the caller then detaches the
    foreground tmux client. A tab launch restores the untouched original tab.
    Legacy layouts create a default-shell tab in the same window first.
    """
    root = _root(root)
    provenance = _origin_identifiers(state)
    preflight = _ownership_preflight(state)
    if provenance is None:
        restore = f'''    if dashboardWindow is missing value then
        error "The dashboard window was closed; its terminal cannot be restored."
    end if
    set returnTab to new tab in dashboardWindow with configuration {{initial working directory:{_literal(root)}, wait after command:false}}
    set returnTerminal to terminal 1 of returnTab
'''
        close = _close_managed()
    else:
        mode, origin_tab, origin_terminal = provenance
        window = _state_identifiers(state)[0]
        restore = _resolve_tab(window, origin_tab, prefix="return") + f'''    if returnTab is missing value then
        error "The original Ghostty tab was closed."
    end if
    if returnIds does not contain {_literal(origin_terminal)} then
        error "The original terminal was closed or moved; leave was cancelled."
    end if
    set returnTerminal to terminal id {_literal(origin_terminal)}
'''
        close = _close_managed(retain=origin_terminal if mode == "inplace" else None)
    return f'''-- Leave only the recorded dashboard and restore its normal terminal.
tell application {_literal(_APP)}
{preflight}{restore}{close}    -- Focus uses the global surface UUID even if closing tabs changed the window container.
    focus returnTerminal
    return "DASHBOARD_NATIVE_LEFT"
end tell
'''


def build_retire_script(state: Mapping[str, Any]) -> str:
    """Close all saved clients after a replacement layout is already ready.

    Missing clients are harmless; a surviving client in another tab cancels
    retirement before any close. This intentionally includes the saved left
    surface, so callers preserving an inplace origin should use Leave instead.
    """
    _origin_identifiers(state)
    return f'''-- Retire only the saved dashboard surfaces, never whole windows/tabs.
tell application {_literal(_APP)}
{_ownership_preflight(state)}{_close_managed()}    return "DASHBOARD_NATIVE_RETIRED"
end tell
'''


def leave(state: Mapping[str, Any], root: str) -> None:
    if _execute(build_leave_script(state, root)).strip() != "DASHBOARD_NATIVE_LEFT":
        raise RuntimeError("Ghostty returned an incomplete leave result")


def retire(state: Mapping[str, Any]) -> None:
    if _execute(build_retire_script(state)).strip() != "DASHBOARD_NATIVE_RETIRED":
        raise RuntimeError("Ghostty returned an incomplete retirement result")


def build_toggle_script(state: Mapping[str, Any], center_command: str, root: str) -> str:
    """Hide the native center client or reattach it between the two side columns.

    The actual recorded surface's existence determines visibility. A center
    surface manually moved to another tab is rejected instead of being closed.
    Ghostty 1.3.1's dedicated ``close`` AppleScript command closes the specified
    surface without confirmation. It does not terminate a detached tmux server.
    """
    root = _root(root)
    command = _text(center_command, label="Center command")
    window, tab, left, center, monitor = _state_identifiers(state)
    return f'''-- Toggle only the recorded dashboard center surface.
tell application {_literal(_APP)}
{_resolve_tab(window, tab)}    if dashboardTab is missing value then
        error "The dashboard tab was closed. Run dashboard again."
    end if
    if dashboardIds does not contain {_literal(left)} or dashboardIds does not contain {_literal(monitor)} then
        error "The dashboard side panels were moved or closed. Run dashboard again."
    end if
    set leftTerminal to terminal id {_literal(left)}
    set monitorTerminal to terminal id {_literal(monitor)}
    set centerId to {_literal(center)}
    set centerExists to false
    if centerId is not "" then
        set centerExists to exists terminal id centerId
    end if
    activate window dashboardWindow
    select tab dashboardTab
    if centerExists then
        if dashboardIds does not contain centerId then
            error "The center terminal was moved to another tab. Move it back before toggling."
        end if
        close terminal id centerId
        focus leftTerminal
        {_result(_literal("-"))}
    else
        set centerTerminal to split monitorTerminal direction left with configuration {_configuration(root, command)}
        focus centerTerminal
        {_result("(id of centerTerminal)")}
    end if
end tell
'''


def parse_state(output: str) -> dict[str, Any]:
    """Parse the versioned line protocol without evaluating AppleScript output."""
    lines = output.strip().splitlines()
    if not ((len(lines) == 6 and lines[0] == _HEADER)
            or (len(lines) == 9 and lines[0] == _HEADER_V2)):
        raise RuntimeError("Ghostty returned an incomplete dashboard layout")
    _, window, tab, left, center, monitor = lines[:6]
    state = {
        "window_id": window,
        "tab_id": tab,
        "terminals": {"left": left, "terminal": "" if center == "-" else center, "monitor": monitor},
        "hidden": center == "-",
    }
    if lines[0] == _HEADER_V2:
        state["origin"] = {"tab_id": lines[6], "terminal_id": lines[7]}
        state["launch_mode"] = lines[8]
    try:
        _state_identifiers(state)
        _origin_identifiers(state)
    except ValueError as error:
        raise RuntimeError("Ghostty returned invalid dashboard identifiers") from error
    return state


def _execute(script: str) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-"], input=script, text=True,
            capture_output=True, timeout=120, check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Native dashboard layouts require macOS and Ghostty 1.3 or newer") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Ghostty did not finish opening the dashboard. Check its macOS permission prompt.") from error
    if result.returncode:
        detail = result.stderr.strip() or "Unknown AppleScript error"
        raise RuntimeError(f"Ghostty could not update the native dashboard: {detail}")
    return result.stdout


def _run(script: str) -> dict[str, Any]:
    # An interrupted/malformed reply is uncertain: do not retry or close other
    # windows. Construction errors are cleaned up by the launch script itself.
    return parse_state(_execute(script))


def launch(root: str, commands: Mapping[str, str], origin_marker: str | None = None) -> dict[str, Any]:
    """Run the generated layout when the user invokes the native dashboard."""
    return _run(build_launch_script(root, commands, origin_marker))


def toggle(state: Mapping[str, Any], center_command: str, root: str) -> dict[str, Any]:
    """Toggle the user-launched native center surface, preserving its tmux session."""
    return _run(build_toggle_script(state, center_command, root))


def build_focus_script(state: Mapping[str, Any], *, activate: bool = True) -> str:
    """Check saved side surfaces and optionally focus the existing dashboard."""
    window, tab, left, center, monitor = _state_identifiers(state)
    activation = f'''
    activate window dashboardWindow
    select tab dashboardTab
    if centerId is not "" and dashboardIds contains centerId then
        focus terminal id centerId
    else
        focus terminal id {_literal(left)}
    end if
''' if activate else ""
    return f'''-- Check only this saved dashboard, never create a new surface.
tell application {_literal(_APP)}
{_resolve_tab(window, tab)}    if dashboardTab is missing value then return "DASHBOARD_NATIVE_MISSING"
    if dashboardIds does not contain {_literal(left)} or dashboardIds does not contain {_literal(monitor)} then
        return "DASHBOARD_NATIVE_MISSING"
    end if
    set centerId to {_literal(center)}
    if centerId is not "" then
        if exists terminal id centerId then
            if dashboardIds does not contain centerId then return "DASHBOARD_NATIVE_MISSING"
        end if
    end if
{activation}    return "DASHBOARD_NATIVE_ALIVE"
end tell
'''


def _probe(state: Mapping[str, Any], *, activate: bool) -> bool:
    reply = _execute(build_focus_script(state, activate=activate)).strip()
    if reply == "DASHBOARD_NATIVE_ALIVE":
        return True
    if reply == "DASHBOARD_NATIVE_MISSING":
        return False
    raise RuntimeError("Ghostty returned an incomplete dashboard status")


def focus(state: Mapping[str, Any]) -> bool:
    """Focus a recorded live dashboard; return False for missing/moved surfaces."""
    return _probe(state, activate=True)


def focus_if_alive(state: Mapping[str, Any]) -> bool:
    """Descriptive alias for ``focus``."""
    return focus(state)


def is_alive(state: Mapping[str, Any]) -> bool:
    """Check a recorded dashboard without changing focus or creating surfaces."""
    return _probe(state, activate=False)


def resize(state, role, direction, amount):
    if direction not in ("left", "right", "up", "down") or not isinstance(amount, int) or not 1 <= amount <= 4096:
        raise ValueError("Invalid resize request")
    target = _identifier(state["terminals"][role], label="Resize terminal identifier")
    script = f'''tell application {_literal(_APP)}
{_ownership_preflight(state)}
    if dashboardIds does not contain {_literal(target)} then error "Resize target is no longer in the dashboard."
    perform action {_literal(f"resize_split:{direction},{amount}")} on terminal id {_literal(target)}
    return "DASHBOARD_RESIZED"
end tell
'''
    if _execute(script).strip() != "DASHBOARD_RESIZED":
        raise RuntimeError("Could not resize dashboard section")


def add_top(state, command, root):
    left = _identifier(state["terminals"]["left"], label="Files terminal identifier")
    config = _configuration(_root(root), _text(command, label="Top command"))
    script = f'''tell application {_literal(_APP)}
{_ownership_preflight(state)}
    if dashboardIds does not contain {_literal(left)} then error "Files is no longer in the dashboard."
    set topTerminal to missing value
    try
        set topTerminal to split terminal id {_literal(left)} direction up with configuration {config}
        return id of topTerminal
    on error failureMessage number failureNumber
        if topTerminal is not missing value then close topTerminal
        error failureMessage number failureNumber
    end try
end tell
'''
    return _identifier(_execute(script).strip(), label="Created top terminal identifier")

"""Native preparation is tested only in disposable tmux; no AppleScript runs."""
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import shlex
import shutil
import signal
import struct
import tempfile
import termios
import time
import unittest
from unittest.mock import patch
import uuid

from dashboard import cli, native, native_workspace as workspace


@unittest.skipUnless(shutil.which("tmux"), "tmux required")
class NativeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dashboard-native-test-")
        self.root = Path(self.temp.name, "project with spaces").resolve()
        self.root.mkdir()
        self.old_socket, self.old_state = cli.SOCKET, cli.STATE
        self.clients = []
        cli.SOCKET = "dashboard-native-test-" + uuid.uuid4().hex[:12]
        cli.STATE = Path(self.temp.name, "state")
        self.addCleanup(self.cleanup_server)
        # Real long-running pane processes, without loading any UI or user code.
        with patch.object(cli, "command", return_value="/bin/sleep 600"):
            self.session = cli.create_workspace(str(self.root), None, 210, 52)
        self.left = cli.option(self.session, "window")
        self.roles = {role: cli.option(self.session, role)
                      for role in ("commits", "files", "terminal", "monitor")}

    def cleanup_server(self):
        try:
            cli.tmux("kill-server", check=False)
            for pid, master in self.clients:
                os.close(master)
                try:
                    waited, _ = os.waitpid(pid, os.WNOHANG)
                    if not waited:
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                except (ChildProcessError, ProcessLookupError):
                    pass
        finally:
            cli.SOCKET, cli.STATE = self.old_socket, self.old_state
            self.temp.cleanup()

    def pane_processes(self):
        result = {}
        for role, pane in self.roles.items():
            pid, dead = cli.tmux("display-message", "-p", "-t", pane,
                                 "#{pane_pid},#{pane_dead}").split(",")
            result[role] = (pane, int(pid), int(dead))
        return result

    def geometry(self):
        rows = cli.tmux("list-panes", "-t", self.left, "-F",
                        "#{pane_id},#{pane_left},#{pane_top},#{pane_width},#{pane_height}")
        return {parts[0]: tuple(map(int, parts[1:]))
                for row in rows.splitlines() if (parts := row.split(","))}

    def session_windows(self, session):
        return set(cli.tmux("list-windows", "-t", session, "-F", "#{window_id}").splitlines())

    def selected_window(self, session):
        return cli.tmux("display-message", "-p", "-t", session, "#{window_id}")

    def native_state(self, *, mode="inplace", generation="old-generation", tty=None, hidden=False):
        state = {"window_id": "window", "tab_id": "tab",
                 "terminals": {"left": "left", "terminal": "" if hidden else "center", "monitor": "monitor"},
                 "hidden": hidden, "generation": generation, "origin_tty": tty}
        if mode is not None:
            state.update(launch_mode=mode, origin={"tab_id": "tab" if mode == "inplace" else "original-tab",
                                                   "terminal_id": "left" if mode == "inplace" else "original-terminal"})
        return state

    def attach_test_client(self, view):
        """Attach a real client in an isolated PTY; never invoke Ghostty."""
        pid, master = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            env = os.environ.copy()
            env.pop("TMUX", None)
            env.pop("TMUX_PANE", None)
            env["TERM"] = "xterm-256color"
            executable = shutil.which("tmux")
            os.execve(executable, [executable, "-L", cli.SOCKET, "attach-session", "-t", view], env)
        self.clients.append((pid, master))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            rows = cli.tmux("list-clients", "-t", view, "-F", "#{client_pid}\t#{client_tty}", check=False)
            for row in rows.splitlines():
                fields = row.split("\t")
                if fields[0] == str(pid) and len(fields) == 2 and fields[1]:
                    return fields[1]
            time.sleep(0.02)
        self.fail("The isolated tmux client did not attach")

    def client_ttys(self, view):
        return set(cli.tmux("list-clients", "-t", view, "-F", "#{client_tty}", check=False).splitlines())

    def test_top_slot_native_migration_and_switch_preserve_processes(self):
        from dashboard import top_section
        workspace.prepare(self.session)
        state = self.native_state()
        workspace.write_state(self.session, state)
        before = self.pane_processes()
        with patch.object(native, "add_top", return_value="top-id") as add:
            updated = top_section.ensure_native(self.session)
            self.assertEqual(updated["terminals"]["top"], "top-id")
            self.assertEqual(updated["origin"], state["origin"])
            self.assertEqual(updated["generation"], state["generation"])
            top_section.ensure_native(self.session)
            add.assert_called_once()
        self.assertEqual(len(self.geometry()), 1)
        view = cli.option(self.session, "view_top")
        slot = self.selected_window(view)
        with patch.object(cli, "command", return_value="/bin/sleep 600"):
            top_section.toggle(self.session)
        notes = cli.option(self.session, "notes")
        notes_pid = cli.tmux("display-message", "-p", "-t", notes, "#{pane_pid}")
        self.assertEqual(cli.option(self.session, "top_mode"), "notes")
        self.assertEqual(cli.tmux("display-message", "-p", "-t", notes, "#{window_id}"), slot)
        top_section.toggle(self.session)
        self.assertEqual(cli.option(self.session, "top_mode"), "commits")
        self.assertEqual(cli.tmux("display-message", "-p", "-t", self.roles["commits"], "#{window_id}"), slot)
        self.assertEqual(cli.tmux("display-message", "-p", "-t", notes, "#{pane_pid}"), notes_pid)
        self.assertEqual(before, self.pane_processes())
        workspace.restore_tmux(self.session)
        self.assertEqual(len(self.geometry()), 4)
        self.assertEqual(before, self.pane_processes())

    def test_top_creation_failure_restores_original_left_layout(self):
        from dashboard import top_section
        workspace.prepare(self.session)
        workspace.write_state(self.session, self.native_state())
        before, layout = self.pane_processes(), self.geometry()
        with patch.object(native, "add_top", side_effect=RuntimeError("mock failure")):
            with self.assertRaisesRegex(RuntimeError, "mock failure"):
                top_section.ensure_native(self.session)
        self.assertEqual(before, self.pane_processes())
        self.assertEqual(layout, self.geometry())

    def test_tmux_notes_swap_preserves_slot_size_and_coding_toggle(self):
        from dashboard import top_section
        before = self.pane_processes()
        original = self.geometry()
        with patch.object(cli, "command", return_value="/bin/sleep 600"):
            top_section.toggle(self.session)
        notes = cli.option(self.session, "notes")
        self.assertEqual(self.geometry()[notes], original[self.roles["commits"]])
        self.assertEqual(self.geometry()[self.roles["files"]], original[self.roles["files"]])
        cli.toggle_terminal(self.session)
        cli.toggle_terminal(self.session)
        top_section.toggle(self.session)
        self.assertEqual(original, self.geometry())
        self.assertEqual(before, self.pane_processes())

    def test_prepare_restore_preserves_every_process_and_custom_layout(self):
        cli.tmux("resize-pane", "-t", self.roles["files"], "-y", "27")
        cli.tmux("resize-pane", "-t", self.roles["terminal"], "-x", "80")
        before_processes, before_geometry = self.pane_processes(), self.geometry()
        self.assertTrue(all(dead == 0 for _, _, dead in before_processes.values()))
        workspace.prepare(self.session)
        self.assertEqual(cli.option(self.session, "backend"), "native")
        self.assertEqual(before_processes, self.pane_processes())
        self.assertEqual(len(self.geometry()), 2)
        self.assertEqual(len(self.session_windows(self.session)), 3)
        workspace.restore_tmux(self.session)
        self.assertEqual(cli.option(self.session, "backend"), "tmux")
        self.assertEqual(before_processes, self.pane_processes())
        self.assertEqual(before_geometry, self.geometry())
        self.assertEqual(len(self.session_windows(self.session)), 1)
        self.assertEqual(cli.tmux("list-sessions", "-F", "#{session_name}"), self.session)

    def test_three_grouped_sessions_select_windows_independently(self):
        workspace.prepare(self.session)
        views = {role: cli.option(self.session, "view_" + role) for role in workspace.ROLES}
        windows = {role: self.selected_window(view) for role, view in views.items()}
        self.assertEqual(len(set(windows.values())), 3)
        for role, view in views.items():
            self.assertEqual(self.session_windows(view), set(windows.values()))
            self.assertEqual(workspace.controller(view), self.session)
            self.assertEqual(cli.option(view, "role"), role)
            self.assertEqual(cli.tmux("show-options", "-v", "-t", view, "status"),
                             "on" if role == "left" else "off")
        cli.tmux("select-window", "-t", views["left"] + ":" + windows["terminal"])
        self.assertEqual(self.selected_window(views["left"]), windows["terminal"])
        self.assertEqual(self.selected_window(views["monitor"]), windows["monitor"])
        self.assertEqual(self.selected_window(views["terminal"]), windows["terminal"])
        self.assertEqual(self.selected_window(self.session), windows["left"])
        for role, command in workspace.attach_commands(self.session).items():
            args = shlex.split(command)
            self.assertEqual(args[-2:], ["-t", views[role]])
            self.assertEqual(args[args.index("-L") + 1], cli.SOCKET)

    def test_hidden_legacy_layout_is_restored_after_rollback(self):
        cli.tmux("resize-pane", "-t", self.roles["files"], "-y", "28")
        cli.toggle_terminal(self.session)
        before_processes, before_geometry = self.pane_processes(), self.geometry()
        self.assertEqual(cli.option(self.session, "hidden"), "1")
        workspace.prepare(self.session)
        self.assertEqual(cli.option(self.session, "hidden"), "0")
        self.assertEqual(cli.option(self.session, "pre_native_hidden"), "1")
        workspace.restore_tmux(self.session, restore_hidden=True)
        self.assertEqual(cli.option(self.session, "hidden"), "1")
        self.assertEqual(before_processes, self.pane_processes())
        self.assertEqual(before_geometry, self.geometry())
        self.assertNotIn(self.roles["terminal"], self.geometry())

    def test_repeated_prepare_is_idempotent(self):
        workspace.prepare(self.session)
        processes = self.pane_processes()
        sessions = cli.tmux("list-sessions", "-F", "#{session_id}:#{session_name}")
        windows = self.session_windows(self.session)
        layout = cli.option(self.session, "pre_native_layout")
        workspace.prepare(self.session)
        self.assertEqual(processes, self.pane_processes())
        self.assertEqual(sessions, cli.tmux("list-sessions", "-F", "#{session_id}:#{session_name}"))
        self.assertEqual(windows, self.session_windows(self.session))
        self.assertEqual(layout, cli.option(self.session, "pre_native_layout"))

    def test_mocked_native_launch_failure_rolls_back_visible_layout(self):
        before_processes, before_geometry = self.pane_processes(), self.geometry()
        with patch.object(native, "launch", side_effect=RuntimeError("simulated launch failure")) as launch, \
                patch.object(native, "focus_if_alive", side_effect=AssertionError("unexpected native focus")):
            with self.assertRaisesRegex(RuntimeError, "simulated launch failure"):
                workspace.open_workspace(self.session)
            launch.assert_called_once()
        self.assertEqual(cli.option(self.session, "backend"), "tmux")
        self.assertEqual(before_processes, self.pane_processes())
        self.assertEqual(before_geometry, self.geometry())
        self.assertEqual(cli.option(self.session, "hidden"), "0")
        self.assertFalse(workspace.state_path(self.session).exists())

    def test_mocked_native_launch_failure_preserves_hidden_terminal(self):
        cli.toggle_terminal(self.session)
        before_processes, before_geometry = self.pane_processes(), self.geometry()
        with patch.object(native, "launch", side_effect=RuntimeError("simulated launch failure")), \
                patch.object(native, "focus_if_alive", side_effect=AssertionError("unexpected native focus")):
            with self.assertRaisesRegex(RuntimeError, "simulated launch failure"):
                workspace.open_workspace(self.session)
        self.assertEqual(cli.option(self.session, "backend"), "tmux")
        self.assertEqual(cli.option(self.session, "hidden"), "1")
        self.assertEqual(before_processes, self.pane_processes())
        self.assertEqual(before_geometry, self.geometry())

    def test_partial_preparation_failure_restores_pane_order(self):
        before_processes, before_geometry = self.pane_processes(), self.geometry()
        real_tmux = cli.tmux

        def fail_monitor_break(*args, **kwargs):
            if args[0] == "break-pane" and self.roles["monitor"] in args:
                raise RuntimeError("simulated preparation failure")
            return real_tmux(*args, **kwargs)

        with patch.object(cli, "tmux", side_effect=fail_monitor_break):
            with self.assertRaisesRegex(RuntimeError, "simulated preparation failure"):
                workspace.prepare(self.session)
        self.assertEqual(cli.option(self.session, "backend"), "tmux")
        self.assertEqual(before_processes, self.pane_processes())
        self.assertEqual(before_geometry, self.geometry())
        self.assertEqual(len(self.session_windows(self.session)), 1)

    def test_inplace_launch_persists_origin_and_generation_without_detaching_clients(self):
        before = self.pane_processes()
        launched = self.native_state(generation="ignored")
        with patch.object(native, "launch", return_value=launched) as launch, \
                patch.object(native, "focus_if_alive") as focus:
            state = workspace.open_workspace(self.session, origin_marker="marker-123", origin_tty="/dev/ttys123")
            launch.assert_called_once_with(str(self.root), workspace.attach_commands(self.session), origin_marker="marker-123")
            focus.assert_not_called()
        self.assertEqual(state["origin_tty"], "/dev/ttys123")
        self.assertNotEqual(state["generation"], "ignored")
        self.assertEqual(state["origin"], launched["origin"])
        self.assertEqual(workspace.read_state(self.session), state)
        self.assertEqual(workspace.state_path(self.session).stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, self.pane_processes())
        with patch.object(native, "focus_if_alive", return_value=True) as focus, patch.object(native, "launch") as launch:
            self.assertIsNone(workspace.open_workspace(self.session))
            focus.assert_called_once_with(state)
            launch.assert_not_called()

    def test_legacy_and_tab_views_are_retired_after_new_inplace_state_is_saved(self):
        workspace.prepare(self.session)
        before = self.pane_processes()
        for mode in (None, "tab"):
            with self.subTest(mode=mode):
                previous = self.native_state(mode=mode, hidden=True)
                workspace.write_state(self.session, previous)

                def retire(saved):
                    self.assertEqual(saved, previous)
                    self.assertNotEqual(workspace.read_state(self.session)["generation"], previous["generation"])

                with patch.object(native, "launch", return_value=self.native_state()), \
                        patch.object(native, "retire", side_effect=retire) as retirement, \
                        patch.object(native, "leave") as leave, \
                        patch.object(native, "focus_if_alive") as focus:
                    state = workspace.open_workspace(self.session, origin_marker="new-origin", origin_tty="/dev/new")
                    retirement.assert_called_once_with(previous)
                    leave.assert_not_called()
                    focus.assert_not_called()
                self.assertEqual(state["launch_mode"], "inplace")
                self.assertEqual(cli.option(self.session, "hidden"), "0")
                self.assertEqual(before, self.pane_processes())

    def test_replacing_inplace_detaches_only_previous_origin_and_preserves_all_pids(self):
        workspace.prepare(self.session)
        view = cli.option(self.session, "view_left")
        origin = self.attach_test_client(view)
        other = self.attach_test_client(view)
        previous = self.native_state(tty=origin)
        workspace.write_state(self.session, previous)
        before = self.pane_processes()
        with patch.object(native, "launch", return_value=self.native_state()), \
                patch.object(native, "leave") as leave, patch.object(native, "retire") as retire, \
                patch.object(native, "focus_if_alive", return_value=True) as focus:
            state = workspace.open_workspace(self.session, origin_marker="replacement", origin_tty="/dev/new")
            leave.assert_called_once_with(previous, str(self.root))
            retire.assert_not_called()
            focus.assert_called_once_with(state)
        self.assertEqual(self.client_ttys(view), {other})
        self.assertEqual(workspace.read_state(self.session), state)
        self.assertEqual(before, self.pane_processes())

    def test_leave_clears_state_before_origin_detach_and_keeps_unrelated_clients(self):
        workspace.prepare(self.session)
        view = cli.option(self.session, "view_left")
        origin, other = self.attach_test_client(view), self.attach_test_client(view)
        state = self.native_state(tty=origin, hidden=True)
        workspace.write_state(self.session, state)
        before = self.pane_processes()
        sessions = cli.tmux("list-sessions", "-F", "#{session_name}")
        real_tmux = cli.tmux

        def observe_detach(*args, **kwargs):
            if args[0] == "detach-client":
                self.assertIsNone(workspace.read_state(self.session))
            return real_tmux(*args, **kwargs)

        with patch.object(native, "leave") as leave, patch.object(cli, "tmux", side_effect=observe_detach):
            workspace.leave_workspace(view, expected_generation=state["generation"])
            workspace.leave_workspace(self.session)
            leave.assert_called_once_with(state, str(self.root))
        self.assertEqual(self.client_ttys(view), {other})
        self.assertEqual(before, self.pane_processes())
        self.assertEqual(cli.option(self.session, "backend"), "native")
        self.assertEqual(cli.option(self.session, "hidden"), "1")
        self.assertEqual(sessions, cli.tmux("list-sessions", "-F", "#{session_name}"))

    def test_stale_generation_and_failed_native_leave_preserve_saved_state(self):
        workspace.prepare(self.session)
        state = self.native_state()
        workspace.write_state(self.session, state)
        before = self.pane_processes()
        with patch.object(native, "leave", side_effect=RuntimeError("ownership changed")) as leave:
            workspace.leave_workspace(self.session, expected_generation="outdated")
            leave.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                workspace.leave_workspace(self.session, expected_generation=state["generation"])
        self.assertEqual(workspace.read_state(self.session), state)
        self.assertEqual(before, self.pane_processes())

    def test_notes_column_preserves_coding_and_origin(self):
        workspace.prepare(self.session)
        state = self.native_state(tty="/dev/origin")
        workspace.write_state(self.session, state)
        coding_pid = cli.tmux("display-message","-p","-t",self.roles["terminal"],"#{pane_pid}")
        reply = self.native_state(mode=None)
        reply["terminals"]["terminal"] = "notes-id"
        with patch.object(cli,"command",return_value="/bin/sleep 600"), patch.object(native,"toggle",return_value=reply):
            workspace.toggle_notes(self.session)
        saved = workspace.read_state(self.session)
        self.assertEqual(saved["terminals"]["terminal"], state["terminals"]["terminal"])
        self.assertEqual(saved["terminals"]["notes"], "notes-id")
        self.assertEqual(saved["origin"],state["origin"])
        self.assertEqual(cli.option(self.session,"notes_hidden"),"0")
        notes_pid = cli.tmux("display-message","-p","-t",cli.option(self.session,"notes"),"#{pane_pid}")
        reply["terminals"]["terminal"] = ""
        reply["hidden"] = True
        with patch.object(native,"toggle",return_value=reply):
            workspace.toggle_notes(self.session)
        self.assertEqual(cli.option(self.session,"notes_hidden"),"1")
        self.assertEqual(cli.tmux("display-message","-p","-t",self.roles["terminal"],"#{pane_pid}"),coding_pid)
        self.assertEqual(cli.tmux("display-message","-p","-t",cli.option(self.session,"notes"),"#{pane_pid}"),notes_pid)

    def test_native_toggle_retains_origin_generation_and_hidden_metadata(self):
        workspace.prepare(self.session)
        state = self.native_state(tty="/dev/origin")
        workspace.write_state(self.session, state)
        before = self.pane_processes()
        for hidden in (True, False):
            with self.subTest(hidden=hidden):
                updated = self.native_state(mode=None, hidden=hidden)
                updated.pop("generation")
                updated.pop("origin_tty")
                with patch.object(native, "toggle", return_value=updated):
                    workspace.toggle_center(self.session)
                current = workspace.read_state(self.session)
                for key in ("generation", "origin_tty", "origin", "launch_mode"):
                    self.assertEqual(current[key], state[key])
                for role in workspace.ROLES:
                    self.assertEqual(cli.option(cli.option(self.session, "view_" + role), "hidden"), "1" if hidden else "0")
                self.assertEqual(before, self.pane_processes())

    def test_state_write_failure_leaves_new_origin_and_restores_old_state(self):
        workspace.prepare(self.session)
        previous = self.native_state(hidden=True)
        workspace.write_state(self.session, previous)
        before = self.pane_processes()
        original_write = workspace.write_state

        def failed_write(session, state):
            original_write(session, state)
            raise OSError("simulated state failure")

        with patch.object(native, "launch", return_value=self.native_state()), \
                patch.object(native, "leave") as leave, patch.object(native, "retire") as retire, \
                patch.object(workspace, "write_state", side_effect=failed_write):
            with self.assertRaisesRegex(OSError, "simulated state failure"):
                workspace.open_workspace(self.session, origin_marker="new")
            self.assertEqual(leave.call_args.args[0]["launch_mode"], "inplace")
            self.assertNotEqual(leave.call_args.args[0]["generation"], previous["generation"])
            retire.assert_not_called()
        self.assertEqual(workspace.read_state(self.session), previous)
        self.assertEqual(cli.option(self.session, "hidden"), "1")
        self.assertEqual(before, self.pane_processes())

    def test_old_ui_retirement_failure_is_nonfatal(self):
        workspace.prepare(self.session)
        workspace.write_state(self.session, self.native_state(mode=None))
        with patch.object(native, "launch", return_value=self.native_state()), \
                patch.object(native, "retire", side_effect=RuntimeError("moved window")), \
                patch.object(workspace.sys, "stderr") as stderr:
            state = workspace.open_workspace(self.session, origin_marker="new")
        self.assertEqual(workspace.read_state(self.session), state)
        self.assertIn("previous view could not close", "".join(call.args[0] for call in stderr.write.call_args_list))

    def test_first_launch_state_failure_restores_hidden_layout_and_borrowed_origin(self):
        cli.toggle_terminal(self.session)
        before, geometry = self.pane_processes(), self.geometry()
        with patch.object(native, "launch", return_value=self.native_state()), \
                patch.object(workspace, "write_state", side_effect=OSError("disk unavailable")), \
                patch.object(native, "leave") as leave, patch.object(native, "retire") as retire:
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                workspace.open_workspace(self.session, origin_marker="origin", origin_tty="/dev/origin")
            leave.assert_called_once()
            self.assertEqual(leave.call_args.args[0]["launch_mode"], "inplace")
            retire.assert_not_called()
        self.assertEqual(cli.option(self.session, "backend"), "tmux")
        self.assertEqual(cli.option(self.session, "hidden"), "1")
        self.assertEqual(self.pane_processes(), before)
        self.assertEqual(self.geometry(), geometry)
        self.assertIsNone(workspace.read_state(self.session))

    def test_leaving_tab_or_legacy_view_does_not_detach_other_left_viewers(self):
        workspace.prepare(self.session)
        view = cli.option(self.session, "view_left")
        viewer = self.attach_test_client(view)
        for mode in (None, "tab"):
            with self.subTest(mode=mode):
                # Even a stale recorded TTY is not ownership of an inplace
                # foreground client for legacy or separately launched tabs.
                state = self.native_state(mode=mode, tty=viewer)
                workspace.write_state(self.session, state)
                with patch.object(native, "leave") as leave:
                    workspace.leave_workspace(self.session)
                    leave.assert_called_once_with(state, str(self.root))
                self.assertEqual(self.client_ttys(view), {viewer})

    def test_attach_runs_without_workspace_lock_and_cleans_up_after_detach(self):
        workspace.prepare(self.session)
        state = self.native_state()
        workspace.write_state(self.session, state)
        before = self.pane_processes()

        def detached(args, *, check):
            self.assertEqual(args, shlex.split(workspace.attach_commands(self.session)["left"]))
            self.assertFalse(check)
            lock_name = hashlib.sha256((self.session + "-native").encode()).hexdigest()[:16] + ".lock"
            with (cli.STATE / lock_name).open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
            return type("Result", (), {"returncode": 7})()

        with patch.object(workspace, "subprocess") as subprocess, patch.object(native, "leave") as leave:
            subprocess.run.side_effect = detached
            self.assertEqual(workspace.attach_origin(self.session, state["generation"]), 7)
            leave.assert_called_once_with(state, str(self.root))
        self.assertIsNone(workspace.read_state(self.session))
        self.assertEqual(before, self.pane_processes())

    def test_attach_failure_cleans_up_but_stale_finally_keeps_replacement(self):
        workspace.prepare(self.session)
        state = self.native_state()
        workspace.write_state(self.session, state)
        with patch.object(workspace, "subprocess") as subprocess, patch.object(native, "leave") as leave:
            subprocess.run.side_effect = OSError("attach failed")
            with self.assertRaisesRegex(OSError, "attach failed"):
                workspace.attach_origin(self.session, state["generation"])
            leave.assert_called_once_with(state, str(self.root))
        replacement = self.native_state(generation="replacement")
        workspace.write_state(self.session, state)

        def replaced_while_attached(*args, **kwargs):
            workspace.write_state(self.session, replacement)
            return type("Result", (), {"returncode": 0})()

        with patch.object(workspace, "subprocess") as subprocess, patch.object(native, "leave") as leave:
            subprocess.run.side_effect = replaced_while_attached
            self.assertEqual(workspace.attach_origin(self.session, state["generation"]), 0)
            subprocess.run.assert_called_once()
            leave.assert_not_called()
        with patch.object(workspace, "subprocess") as subprocess, patch.object(native, "leave") as leave:
            subprocess.run.return_value.returncode = 0
            self.assertEqual(workspace.attach_origin(self.session, state["generation"]), 0)
            subprocess.run.assert_not_called()
            leave.assert_not_called()
        self.assertEqual(workspace.read_state(self.session), replacement)

    def test_attach_skips_missing_or_separately_launched_tab_state(self):
        workspace.prepare(self.session)
        for mode in (None, "tab"):
            with self.subTest(mode=mode):
                if mode is not None:
                    workspace.write_state(self.session, self.native_state(mode=mode))
                with patch.object(workspace, "subprocess") as subprocess, patch.object(native, "leave") as leave:
                    self.assertEqual(workspace.attach_origin(self.session, "old-generation"), 0)
                    subprocess.run.assert_not_called()
                    leave.assert_not_called()


if __name__ == "__main__":
    unittest.main()

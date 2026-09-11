"""Launch/return control flow using a private pseudo-terminal and mocked GUI.

These tests never attach to a user terminal, send AppleScript, or start tmux.
The foreground-process check is mocked because the private PTY is deliberately
not made this test process's controlling terminal.
"""

import contextlib
import io
import os
from pathlib import Path
import pty
import unittest
from unittest.mock import patch

from dashboard import cli, native, native_workspace


class TerminalCapture:
    def __init__(self, master: int, slave: int) -> None:
        self.master = master
        self.tty = os.ttyname(slave)
        self.output = bytearray()

    def read(self) -> bytes:
        while True:
            try:
                data = os.read(self.master, 65536)
            except BlockingIOError:
                break
            if not data:
                break
            self.output.extend(data)
        return bytes(self.output)


@contextlib.contextmanager
def private_terminal():
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    capture = TerminalCapture(master, slave)
    try:
        with contextlib.ExitStack() as stack:
            incoming = stack.enter_context(os.fdopen(os.dup(slave), "r", encoding="utf-8"))
            outgoing = stack.enter_context(os.fdopen(os.dup(slave), "w", encoding="utf-8", buffering=1))
            stack.enter_context(patch.object(cli.sys, "stdin", incoming))
            stack.enter_context(patch.object(cli.sys, "stdout", outgoing))
            stack.enter_context(patch.object(cli.os, "tcgetpgrp", return_value=os.getpgrp()))
            stack.enter_context(patch.dict(os.environ, {"TERM_PROGRAM": "ghostty", "TMUX": "", "TMUX_PANE": ""}))
            yield capture
    finally:
        os.close(slave)
        os.close(master)


def title_sequence(value: str) -> bytes:
    return b"\x1b]2;" + value.encode("utf-8") + b"\x07"


class OriginTitleTests(unittest.TestCase):
    def test_marks_only_the_calling_tty_then_explicitly_restores_title(self) -> None:
        with private_terminal() as terminal, patch.object(cli.Path, "cwd", return_value=Path("/mock/My Project")):
            with cli.marked_origin() as (marker, tty):
                self.assertRegex(marker, r"^dashboard-origin-[0-9a-f]{32}$")
                self.assertEqual(tty, terminal.tty)
                self.assertEqual(terminal.read(), title_sequence(marker))
            self.assertEqual(terminal.read(), title_sequence(marker) + title_sequence("My Project"))
            self.assertNotIn(b"\x1b[22", terminal.output)
            self.assertNotIn(b"\x1b[23", terminal.output)

    def test_title_restoration_runs_on_body_failure_and_strips_control_characters(self) -> None:
        unsafe_name = "Project\x1b]2;unexpected\x07\n\x7f"
        expected = "Project]2;unexpected"
        with private_terminal() as terminal, patch.object(cli.Path, "cwd", return_value=Path("/mock") / unsafe_name):
            with self.assertRaisesRegex(RuntimeError, "lookup failed"):
                with cli.marked_origin():
                    raise RuntimeError("lookup failed")
            output = terminal.read()
            self.assertTrue(output.endswith(title_sequence(expected)))
            self.assertEqual(output.count(b"\x1b]2;"), 2)

    def test_preflight_failures_do_not_write_any_title(self) -> None:
        cases = (
            ("other app", lambda: patch.dict(os.environ, {"TERM_PROGRAM": "Terminal"})),
            ("redirected input", lambda: patch.object(cli.sys, "stdin", io.StringIO())),
            ("redirected output", lambda: patch.object(cli.sys, "stdout", io.StringIO())),
            ("different terminals", lambda: patch.object(cli.os, "ttyname", side_effect=["/dev/one", "/dev/two"])),
            ("background command", lambda: patch.object(cli.os, "tcgetpgrp", return_value=os.getpgrp() + 1)),
        )
        for name, replacement in cases:
            with self.subTest(name=name), private_terminal() as terminal, replacement():
                with self.assertRaises(RuntimeError):
                    with cli.marked_origin():
                        self.fail("Unsafe origin passed preflight")
                self.assertEqual(terminal.read(), b"")


class NativeLaunchFlowTests(unittest.TestCase):
    def setUp(self):
        migration = patch("dashboard.top_section.ensure_native")
        migration.start()
        self.addCleanup(migration.stop)

    def test_inplace_launch_restores_title_then_waits_for_attach_and_returns(self) -> None:
        phases = []
        state = {"launch_mode": "inplace", "generation": "generation-123"}
        with private_terminal() as terminal, patch.object(cli.Path, "cwd", return_value=Path("/mock/project")):
            def open_workspace(session, *, origin_marker, origin_tty):
                phases.append("open")
                self.assertEqual(session, "test-workspace")
                self.assertEqual(origin_tty, terminal.tty)
                self.assertEqual(terminal.read(), title_sequence(origin_marker))
                return state

            def attach_origin(session, generation):
                phases.append("attach")
                self.assertEqual((session, generation), ("test-workspace", "generation-123"))
                self.assertTrue(terminal.read().endswith(title_sequence("project")))
                self.assertNotIn(b"\x1b[2J", terminal.output)
                return 7

            with patch.object(native_workspace, "open_workspace", side_effect=open_workspace), \
                 patch.object(native_workspace, "attach_origin", side_effect=attach_origin), \
                 patch.object(cli.os, "execvpe") as replace_process:
                result = cli.run_native_workspace("test-workspace")
            phases.append("returned")
            self.assertEqual(result, 7)
            self.assertEqual(phases, ["open", "attach", "returned"])
            self.assertTrue(terminal.read().endswith(b"\x1b[0m\x1b[2J\x1b[H"))
            self.assertEqual(os.ttyname(cli.sys.stdin.fileno()), terminal.tty)
            replace_process.assert_not_called()

    def test_tab_launch_restores_title_and_returns_without_attaching_origin(self) -> None:
        with private_terminal() as terminal, \
             patch.object(native_workspace, "open_workspace", return_value={"launch_mode": "tab", "generation": "g"}) as open_workspace, \
             patch.object(native_workspace, "attach_origin") as attach:
            self.assertEqual(cli.run_native_workspace("test-workspace"), 0)
            attach.assert_not_called()
            self.assertEqual(open_workspace.call_args.kwargs["origin_tty"], terminal.tty)
            self.assertEqual(terminal.read().count(b"\x1b]2;"), 2)
            self.assertNotIn(b"\x1b[2J", terminal.output)

    def test_open_failure_cleans_marker_without_trying_to_attach(self) -> None:
        with private_terminal() as terminal, patch.object(cli.Path, "cwd", return_value=Path("/mock/project")), \
             patch.object(native_workspace, "open_workspace", side_effect=RuntimeError("No matching surface")), \
             patch.object(native_workspace, "attach_origin") as attach:
            with self.assertRaisesRegex(RuntimeError, "No matching surface"):
                cli.run_native_workspace("test-workspace")
            self.assertTrue(terminal.read().endswith(title_sequence("project")))
            self.assertNotIn(b"\x1b[2J", terminal.output)
            attach.assert_not_called()

    def test_attach_failure_and_interrupt_restore_screen_before_returning_to_shell(self) -> None:
        for error in (RuntimeError("Attach failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), private_terminal() as terminal, \
                 patch.object(cli.Path, "cwd", return_value=Path("/mock/project")), \
                 patch.object(native_workspace, "open_workspace", return_value={"launch_mode": "inplace", "generation": "g"}), \
                 patch.object(native_workspace, "attach_origin", side_effect=error):
                with self.assertRaises(type(error)):
                    cli.run_native_workspace("test-workspace")
                output = terminal.read()
                self.assertIn(title_sequence("project"), output)
                self.assertTrue(output.endswith(b"\x1b[0m\x1b[2J\x1b[H"))

    def test_own_tmux_reentry_focuses_existing_workspace_without_marking_origin(self) -> None:
        state = {"launch_mode": "inplace", "generation": "existing-generation"}
        with patch.object(cli, "marked_origin", side_effect=AssertionError("Must not mark coding terminal")), \
             patch.object(native_workspace, "read_state", return_value=state), \
             patch.object(cli, "option", return_value="native"), \
             patch.object(native, "focus_if_alive", return_value=True) as focus, \
             patch.object(native_workspace, "open_workspace") as open_workspace, \
             patch.object(native_workspace, "attach_origin") as attach:
            self.assertEqual(cli.run_native_workspace("test-workspace", inside_tmux=True), 0)
            focus.assert_called_once_with(state)
            open_workspace.assert_not_called()
            attach.assert_not_called()

    def test_own_tmux_reentry_never_creates_an_origin_if_existing_layout_is_unavailable(self) -> None:
        cases = ((None, "native", True), ({"generation": "g"}, "tmux", True), ({"generation": "g"}, "native", False))
        for state, backend, alive in cases:
            with self.subTest(state=state, backend=backend, alive=alive), \
                 patch.object(cli, "marked_origin", side_effect=AssertionError("Must not mark coding terminal")), \
                 patch.object(native_workspace, "read_state", return_value=state), \
                 patch.object(cli, "option", return_value=backend), \
                 patch.object(native, "focus_if_alive", return_value=alive), \
                 patch.object(native_workspace, "open_workspace") as open_workspace:
                with self.assertRaisesRegex(RuntimeError, "normal Ghostty terminal"):
                    cli.run_native_workspace("test-workspace", inside_tmux=True)
                open_workspace.assert_not_called()


class MainLaunchDispatchTests(unittest.TestCase):
    def launch_mocks(self):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(cli.sys, "platform", "darwin"))
        stack.enter_context(patch.object(cli.shutil, "which", return_value="/mock/bin/tool"))
        stack.enter_context(patch("dashboard.gitdata.discover_root", return_value="/mock/project"))
        stack.enter_context(patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((40, 20))))
        create = stack.enter_context(patch.object(cli, "create_workspace", return_value="test-workspace"))
        launch = stack.enter_context(patch.object(cli, "run_native_workspace", return_value=0))
        return stack, create, launch

    def test_main_routes_regular_narrow_ghostty_to_origin_launcher(self) -> None:
        stack, create, launch = self.launch_mocks()
        with private_terminal(), stack:
            self.assertEqual(cli.main([]), 0)
            create.assert_called_once_with("/mock/project", None, 180, 48)
            launch.assert_called_once_with("test-workspace", inside_tmux=False)

    def test_main_routes_only_own_tmux_socket_to_nonorigin_reentry(self) -> None:
        stack, create, launch = self.launch_mocks()
        socket = "/mock/tmux/dashboard"
        def tmux(*args, **kwargs):
            if args[-1] == "#{socket_path}":
                return socket
            if args[-1] == "#{window_width},#{window_height}":
                return "50,25"
            raise AssertionError(f"Unexpected tmux call: {args}")
        with private_terminal(), stack, patch.dict(os.environ, {"TMUX": socket + ",123,0", "TMUX_PANE": "%7"}), \
             patch.object(cli, "tmux", side_effect=tmux):
            self.assertEqual(cli.main([]), 0)
            launch.assert_called_once_with("test-workspace", inside_tmux=True)
            create.assert_called_once_with("/mock/project", None, 180, 48)

    def test_main_rejects_foreign_tmux_before_changing_workspace(self) -> None:
        stack, create, launch = self.launch_mocks()
        errors = io.StringIO()
        with private_terminal(), stack, patch.dict(os.environ, {"TMUX": "/mock/unrelated,123,0"}), \
             patch.object(cli, "tmux", return_value="/mock/dashboard"), patch.object(cli.sys, "stderr", errors):
            self.assertEqual(cli.main([]), 1)
            create.assert_not_called()
            launch.assert_not_called()
            self.assertIn("outside an existing tmux session", errors.getvalue())


if __name__ == "__main__":
    unittest.main()

"""Actual tmux status mouse events through a disposable PTY, never Ghostty UI.

pyte is an optional development-only terminal parser; it is not a dashboard
runtime dependency. The repository's development environment provides it.
"""
import fcntl
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import tempfile
import termios
import time
import unittest
from unittest.mock import patch
import uuid

try:
    import pyte
except ImportError:
    pyte = None

from dashboard import cli, native_workspace


if pyte is not None:
    class RecordingScreen(pyte.Screen):
        """Answer tmux's terminal queries, including its private cursor query."""
        writer = None

        def write_process_input(self, value):
            if self.writer:
                self.writer(value.encode())

        def report_device_status(self, mode=0, private=False):
            if mode == 6:
                prefix = "?" if private else ""
                self.write_process_input(f"\x1b[{prefix}{self.cursor.y + 1};{self.cursor.x + 1}R")
            elif not private:
                super().report_device_status(mode)


@unittest.skipUnless(shutil.which("tmux") and pyte is not None,
                     "tmux and optional dev-only pyte are required")
class StatusMouseTests(unittest.TestCase):
    WIDTH, HEIGHT = 180, 42

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dashboard-click-test-")
        self.root = Path(self.temp.name, "project with spaces").resolve()
        self.root.mkdir()
        self.old_socket, self.old_state = cli.SOCKET, cli.STATE
        cli.SOCKET = "dashboard-click-test-" + uuid.uuid4().hex[:12]
        cli.STATE = Path(self.temp.name, "state")
        self.child_pid = None
        self.master_fd = None
        self.addCleanup(self.cleanup)
        # Real status actions run in a child Python process whose directory is
        # the fixture; preserve importability without installing into the user.
        project = str(Path(__file__).resolve().parents[1])
        pythonpath = project + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
        self.env_patch = patch.dict(os.environ, {"PYTHONPATH": pythonpath})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        actual_command = cli.command

        def test_command(*args):
            if args[0] in ("_panel", "_monitor"):
                return "/bin/sleep 600"
            return actual_command(*args)

        with patch.object(cli, "command", side_effect=test_command):
            self.session = cli.create_workspace(str(self.root), None, self.WIDTH, self.HEIGHT)
        self.terminal = cli.option(self.session, "terminal")
        self.pid = cli.tmux("display-message", "-p", "-t", self.terminal, "#{pane_pid}")
        self.screen = RecordingScreen(self.WIDTH, self.HEIGHT)
        self.stream = pyte.ByteStream(self.screen)
        pid, master = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", self.HEIGHT, self.WIDTH, 0, 0))
            env = os.environ.copy()
            env.pop("TMUX", None)
            env.pop("TMUX_PANE", None)
            env.pop("NO_COLOR", None)
            env.update(TERM="xterm-256color", COLORTERM="truecolor")
            executable = shutil.which("tmux")
            os.execve(executable, [executable, "-L", cli.SOCKET, "attach-session", "-t", self.session], env)
        self.child_pid, self.master_fd = pid, master
        self.screen.writer = lambda value: os.write(master, value)
        os.set_blocking(master, False)
        self.wait_for(lambda: "Hide coding terminal" in self.status_row())
        self.client = cli.tmux("list-clients", "-t", self.session, "-F", "#{client_name}")
        self.assertTrue(self.client)

    def cleanup(self):
        try:
            cli.tmux("kill-server", check=False)
            if self.master_fd is not None:
                os.close(self.master_fd)
                self.master_fd = None
            if self.child_pid:
                try:
                    waited, _ = os.waitpid(self.child_pid, os.WNOHANG)
                    if waited == 0:
                        # A tmux client can wait on terminal-query replies even
                        # after server exit; cleanup must never block the suite.
                        os.kill(self.child_pid, signal.SIGKILL)
                        os.waitpid(self.child_pid, 0)
                except (ChildProcessError, ProcessLookupError):
                    pass
        finally:
            cli.SOCKET, cli.STATE = self.old_socket, self.old_state
            self.temp.cleanup()

    def pump(self, duration=0.04):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master_fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                return
            try:
                chunk = os.read(self.master_fd, 65536)
            except (BlockingIOError, OSError):
                return
            if not chunk:
                return
            self.stream.feed(chunk)

    def wait_for(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if condition():
                return
        self.fail("Timed out waiting for tmux status action. Last row: " + self.status_row())

    def status_row(self):
        return self.screen.display[-1]

    def click_column(self, column):
        # SGR mouse press and release, using one-based terminal coordinates.
        position = f"{column + 1};{self.HEIGHT}"
        os.write(self.master_fd, f"\x1b[<0;{position}M\x1b[<0;{position}m".encode())

    def click_label(self, label):
        row = self.status_row()
        self.assertIn(label, row)
        self.click_column(row.index(label) + len(label) // 2)

    def assert_terminal_preserved(self):
        self.assertEqual(cli.tmux("display-message", "-p", "-t", self.terminal, "#{pane_pid}"), self.pid)
        self.assertEqual(cli.tmux("display-message", "-p", "-t", self.terminal, "#{pane_dead}"), "0")

    def test_rendered_toggle_click_hides_and_shows_same_process(self):
        self.click_label("Hide coding terminal")
        self.wait_for(lambda: cli.option(self.session, "hidden") == "1" and "Show coding terminal" in self.status_row())
        self.assert_terminal_preserved()
        # These are distinct clicks, not tmux's DoubleClick1Status gesture.
        self.pump(0.55)
        self.click_label("Show coding terminal")
        self.wait_for(lambda: cli.option(self.session, "hidden") == "0" and "Hide coding terminal" in self.status_row())
        self.assert_terminal_preserved()

    def test_blank_status_click_is_inert(self):
        self.assertEqual(self.status_row()[self.WIDTH // 2], " ")
        self.click_column(self.WIDTH // 2)
        self.pump(0.7)
        self.assertEqual(cli.option(self.session, "hidden"), "0")
        self.assertIn("Hide coding terminal", self.status_row())
        self.assertEqual(cli.tmux("list-clients", "-t", self.session, "-F", "#{client_name}"), self.client)
        self.assert_terminal_preserved()

    def test_help_click_opens_popup_without_toggling(self):
        self.click_label("Help")
        self.wait_for(lambda: any("DASHBOARD CONTROLS" in row for row in self.screen.display))
        self.assertEqual(cli.option(self.session, "hidden"), "0")
        self.assert_terminal_preserved()
        os.write(self.master_fd, b"\r")
        self.wait_for(lambda: not any("DASHBOARD CONTROLS" in row for row in self.screen.display))
        self.assertIn("Hide coding terminal", self.status_row())

    def test_leave_click_detaches_client_without_killing_process(self):
        self.click_label("Leave")
        self.wait_for(lambda: not cli.tmux("list-clients", "-t", self.session, "-F", "#{client_name}", check=False))
        self.assert_terminal_preserved()
        self.assertEqual(cli.option(self.session, "hidden"), "0")

    def test_detach_shortcut_uses_leave_path_and_preserves_process(self):
        os.write(self.master_fd, b"\x02d")
        self.wait_for(lambda: not cli.tmux("list-clients", "-t", self.session,
                                         "-F", "#{client_name}", check=False))
        self.assert_terminal_preserved()

    def test_footer_labels_remain_separate_across_sizes_and_toggle_states(self):
        for width in (24, 30, 31, 36, 39, 40, 58, 63, 64, 90, 180):
            self.screen.resize(self.HEIGHT,width)
            fcntl.ioctl(self.master_fd,termios.TIOCSWINSZ,struct.pack("HHHH",self.HEIGHT,width,0,0))
            for hidden in ('0','1'):
                cli.set_option(self.session,'hidden',hidden)
                cli.set_option(self.session,'top_mode','commits' if hidden=='1' else 'notes')
                cli.tmux('refresh-client','-S','-t',self.client)
                coding=('Show' if hidden=='1' else 'Hide') + (' coding terminal' if width>=64 else ' coding') if width>=31 else 'Code'
                notes='Notes' if hidden=='1' else 'Commits'
                self.wait_for(lambda: coding in self.status_row() and notes in self.status_row() and 'Leave' in self.status_row())
                row=self.status_row()
                self.assertLess(row.index(coding)+len(coding),row.index(notes))
                self.assertLess(row.index(notes)+len(notes),row.index('Leave'))
        self.assert_terminal_preserved()

    def test_leave_remains_clickable_in_a_narrow_column(self):
        self.screen.resize(lines=self.HEIGHT, columns=31)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.HEIGHT, 31, 0, 0))
        self.wait_for(lambda: "Leave" in self.status_row() and "Help" not in self.status_row())
        self.click_label("Leave")
        self.wait_for(lambda: not cli.tmux("list-clients", "-t", self.session,
                                         "-F", "#{client_name}", check=False))
        self.assert_terminal_preserved()


class NativeStatusDispatchTests(unittest.TestCase):
    def test_native_leave_button_and_shortcut_share_origin_cleanup(self):
        def option(session, key):
            return {("left-view", "parent"): "controller", ("controller", "backend"): "native"}.get((session, key), "")

        with patch.object(cli, "option", side_effect=option), \
                patch.object(cli, "tmux") as tmux, \
                patch.object(native_workspace, "leave_workspace") as leave:
            cli.status_click("left-view", "leave", "/dev/ttys-test")
            leave.assert_called_once_with("controller")
            leave.reset_mock()
            self.assertEqual(cli.main(["_leave", "--session", "left-view", "--client", "/dev/ttys-test"]), 0)
            leave.assert_called_once_with("controller")
            tmux.assert_not_called()

    def test_native_terminal_dispatch_is_mocked_and_unknown_controls_are_inert(self):
        def option(session, key):
            return "native" if key == "backend" else ""

        with patch.object(cli, "tmux", return_value="$1"), \
                patch.object(cli, "option", side_effect=option), \
                patch.object(native_workspace, "toggle_center") as toggle:
            cli.status_click("$1", "terminal", "client")
            toggle.assert_called_once_with("$1")
            toggle.reset_mock()
            for control in ("", "not-a-button", "window"):
                cli.status_click("$1", control, "client")
            toggle.assert_not_called()


if __name__ == "__main__":
    unittest.main()

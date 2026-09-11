"""Exercise real tmux panes, process continuity, and fixed-position controls."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from dashboard import cli


@unittest.skipUnless(shutil.which("tmux"), "tmux required")
class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="dashboard-layout-")
        cls.root = Path(cls.temp.name) / "project with spaces"
        cls.root.mkdir()
        cls.previous_socket, cls.previous_state = cli.SOCKET, cli.STATE
        cls.test_socket = "dashboard-test-" + uuid.uuid4().hex[:10]
        cls.resource_socket = cls.test_socket + "-resources"
        cli.SOCKET = cls.test_socket
        cli.STATE = Path(cls.temp.name) / "state"
        # _monitor starts its own resource server in a child Python process.
        # Module globals alone cannot isolate that process or its state files.
        cls.environment = patch.dict(os.environ, {
            "DASHBOARD_SOCKET": cls.test_socket,
            "DASHBOARD_RESOURCE_SOCKET": cls.resource_socket,
            "DASHBOARD_STATE_DIR": str(cli.STATE),
        })
        cls.environment.start()
        cls.addClassCleanup(cls.cleanup_workspace)
        subprocess.run(["git", "init", "-b", "main", str(cls.root)], check=True, capture_output=True)
        (cls.root / "hello.txt").write_text("hello\n")
        subprocess.run(["git", "-C", str(cls.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(cls.root), "-c", "user.name=Dashboard Test", "-c",
                        "user.email=test@example.invalid", "commit", "-m", "Initial"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(cls.root), "switch", "-c", "feature/test"], check=True, capture_output=True)
        cls.session = cli.create_workspace(str(cls.root), None, 210, 52)

    @classmethod
    def cleanup_workspace(cls):
        try:
            # Target saved test-only socket names even if setup fails halfway.
            for socket in (cls.test_socket, cls.resource_socket):
                subprocess.run(["tmux", "-L", socket, "kill-server"],
                               capture_output=True, timeout=5, check=False)
        finally:
            cli.SOCKET, cli.STATE = cls.previous_socket, cls.previous_state
            cls.environment.stop()
            cls.temp.cleanup()

    def panes(self):
        data = cli.tmux("list-panes", "-t", cli.option(self.session, "window"), "-F",
                        "#{pane_id},#{pane_left},#{pane_top},#{pane_width},#{pane_height},#{pane_pid}")
        return {parts[0]: tuple(map(int, parts[1:])) for row in data.splitlines() if (parts := row.split(","))}

    def test_00_nested_monitor_uses_only_isolated_resources_and_state(self):
        self.assertEqual(cli.tmux("show-environment", "-g", "DASHBOARD_RESOURCE_SOCKET"),
                         "DASHBOARD_RESOURCE_SOCKET=" + self.resource_socket)
        self.assertEqual(cli.tmux("show-environment", "-g", "DASHBOARD_STATE_DIR"),
                         "DASHBOARD_STATE_DIR=" + str(cli.STATE))
        if not shutil.which("btop"):
            self.skipTest("btop required to verify the nested resource process")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            probe = subprocess.run(["tmux", "-L", self.resource_socket, "list-sessions", "-F", "#{session_name}"],
                                   capture_output=True, text=True, timeout=2)
            if probe.returncode == 0 and "resources-" in probe.stdout:
                break
            time.sleep(0.05)
        else:
            self.fail("The monitor did not start on its isolated resource socket.")
        self.assertEqual(cli.tmux("display-message", "-p", "-t", cli.option(self.session, "monitor"),
                                  "#{pane_dead}"), "0")

    def test_01_positions_and_idempotent_launch(self):
        panes = self.panes()
        c, f, t, b = (panes[cli.option(self.session, role)] for role in ("commits", "files", "terminal", "monitor"))
        self.assertEqual(len(panes), 4)
        self.assertEqual(c[0], f[0])
        self.assertEqual(c[2], f[2])
        self.assertLess(c[1], f[1])
        self.assertLess(c[0], t[0])
        self.assertLess(t[0], b[0])
        self.assertEqual(t[1], b[1])
        self.assertEqual(t[3], b[3])
        self.assertEqual(self.session, cli.create_workspace(str(self.root), None, 210, 52))
        self.assertEqual(panes, self.panes())

    def test_02_toggle_preserves_process_and_custom_dimensions(self):
        window = cli.option(self.session, "window")
        term = cli.option(self.session, "terminal")
        cli.tmux("resize-pane", "-t", cli.option(self.session, "files"), "-y", "27")
        cli.tmux("resize-pane", "-t", term, "-x", "80")
        before = self.panes()
        pid = before[term][-1]
        cli.toggle_terminal(self.session)
        self.assertEqual(len(self.panes()), 3)
        self.assertNotIn(term, self.panes())
        self.assertEqual(cli.option(self.session, "hidden"), "1")
        self.assertEqual(int(cli.tmux("display-message", "-p", "-t", term, "#{pane_pid}")), pid)
        self.assertEqual(cli.tmux("display-message", "-p", "-t", term, "#{pane_dead}"), "0")
        cli.toggle_terminal(self.session)
        self.assertEqual(cli.option(self.session, "hidden"), "0")
        self.assertEqual(before, self.panes())
        self.assertEqual(cli.tmux("display-message", "-p", "-t", window, "#{pane_id}"), term)

    def test_03_toggle_after_window_resize(self):
        window = cli.option(self.session, "window")
        term = cli.option(self.session, "terminal")
        cli.toggle_terminal(self.session)
        cli.tmux("resize-window", "-t", window, "-x", "160", "-y", "40")
        cli.toggle_terminal(self.session)
        panes = self.panes()
        self.assertEqual(len(panes), 4)
        self.assertLess(panes[cli.option(self.session, "commits")][0], panes[term][0])
        self.assertLess(panes[term][0], panes[cli.option(self.session, "monitor")][0])
        self.assertLessEqual(max(p[0] + p[2] for p in panes.values()), 160)

    def test_04_movement_bindings_disabled_resize_enabled(self):
        keys = cli.tmux("list-keys")
        for action in ("swap-pane", "rotate-window", "move-pane", "display-menu", "next-layout"):
            self.assertNotIn(action, keys)
        self.assertIn("MouseDrag1Border", keys)
        self.assertIn("resize-pane -M", keys)
        self.assertIn("F2", keys)
        self.assertIn("C-Right", keys)

    def test_05_shell_keeps_project_directory(self):
        pane = cli.option(self.session, "terminal")
        current = cli.tmux("display-message", "-p", "-t", pane, "#{pane_current_path}")
        self.assertEqual(Path(current).resolve(), self.root.resolve())

    def test_06_piped_launch_does_not_create_a_background_workspace(self):
        import sys
        fresh_socket = cli.SOCKET + "-noninteractive"
        env = os.environ.copy()
        env.pop("TMUX", None)
        env["DASHBOARD_SOCKET"] = fresh_socket
        env["DASHBOARD_STATE_DIR"] = str(cli.STATE)
        result = subprocess.run([sys.executable, "-m", "dashboard.cli", str(self.root)],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("interactive terminal", result.stderr)
        probe = subprocess.run(["tmux", "-L", fresh_socket, "list-sessions"], capture_output=True)
        self.assertNotEqual(probe.returncode, 0)

    def test_07_toggle_command_works_from_a_narrow_existing_pane(self):
        import sys
        env = os.environ.copy()
        env.pop("TMUX", None)
        env["DASHBOARD_SOCKET"] = cli.SOCKET
        env["DASHBOARD_STATE_DIR"] = str(cli.STATE)
        for expected in ("1", "0"):
            result = subprocess.run([sys.executable, "-m", "dashboard.cli", str(self.root),
                                     "--terminal", "--size", "60x30"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cli.option(self.session, "hidden"), expected)


if __name__ == "__main__":
    unittest.main()

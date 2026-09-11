"""Check the isolated btop/process stack without touching a live dashboard."""
from pathlib import Path
import shlex
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from dashboard import resource_stack as stack


@unittest.skipUnless(shutil.which("tmux") and shutil.which("btop"), "tmux and btop required")
class ResourceStackTests(unittest.TestCase):
    def test_graphs_above_processes_and_resize_survives_reattach(self):
        socket = "resource-stack-test-" + uuid.uuid4().hex[:10]
        with tempfile.TemporaryDirectory(prefix="resource-stack-") as temporary:
            try:
                directory = Path(temporary)
                stack.prepare(directory, socket, "check", 68, 55, initial_view="gpu")
                upper = stack.tmux(socket, "show-options", "-qv", "-t", "check", "@resource_btop")
                lower = stack.tmux(socket, "show-options", "-qv", "-t", "check", "@resource_processes")
                self.assertNotEqual(upper, lower)
                for pane in (upper, lower):
                    self.assertEqual(stack.tmux(socket, "display-message", "-p", "-t", pane, "#{pane_dead}"), "0")
                self.assertEqual(stack.tmux(socket, "display-message", "-p", "-t", upper, "#{pane_top}"), "0")
                self.assertGreater(int(stack.tmux(socket, "display-message", "-p", "-t", lower, "#{pane_top}")), 0)
                self.assertIn("btop", stack.tmux(socket, "display-message", "-p", "-t", upper, "#{pane_start_command}"))
                lower_command = stack.tmux(socket, "display-message", "-p", "-t", lower, "#{pane_start_command}")
                command_args = shlex.split(lower_command)
                if len(command_args) == 1:
                    command_args = shlex.split(command_args[0])  # tmux quotes the complete shell command.
                self.assertIn("--process-table", command_args)
                self.assertEqual(command_args[command_args.index("--view") + 1], "gpu")
                stack.tmux(socket, "resize-pane", "-t", lower, "-y", "20")
                layout = stack.tmux(socket, "display-message", "-p", "-t", "check", "#{window_layout}")
                pids = stack.tmux(socket, "list-panes", "-t", "check", "-F", "#{pane_pid}")
                stack.prepare(directory, socket, "check", 68, 55, restart=False, initial_view="gpu")
                self.assertEqual(layout, stack.tmux(socket, "display-message", "-p", "-t", "check", "#{window_layout}"))
                self.assertEqual(pids, stack.tmux(socket, "list-panes", "-t", "check", "-F", "#{pane_pid}"))
                self.assertEqual(lower_command, stack.tmux(socket, "display-message", "-p", "-t", lower,
                                                           "#{pane_start_command}"))
                keys = stack.tmux(socket, "list-keys")
                self.assertIn("resize-pane -M", keys)
                self.assertNotIn("swap-pane", keys)
                self.assertNotIn("display-menu", keys)
                # Reattaching reloads the config even after empty key tables
                # have been removed by tmux.
                stack.prepare(directory, socket, "check", 68, 55, initial_view="gpu")
                self.assertEqual(layout, stack.tmux(socket, "display-message", "-p", "-t", "check", "#{window_layout}"))
            finally:
                stack.tmux(socket, "kill-server", check=False)


class ResourceStackEntryTests(unittest.TestCase):
    def test_gpu_view_reaches_process_table_entry(self):
        with patch.object(stack, "run_table") as table, patch.object(stack, "run") as stack_run:
            stack.main(["--process-table", "--view", "gpu"])
        table.assert_called_once_with(initial_view="gpu")
        stack_run.assert_not_called()

    def test_memory_view_reaches_full_stack_entry(self):
        with patch.object(stack, "run_table") as table, patch.object(stack, "run") as stack_run:
            stack.main(["--view", "memory"])
        table.assert_not_called()
        stack_run.assert_called_once_with(initial_view="memory")


if __name__ == "__main__":
    unittest.main()

"""Verify script safety/state handling without invoking any Ghostty UI action."""
import subprocess
import unittest
from unittest.mock import patch

from dashboard import native


COMMANDS = {
    "left": "/opt/homebrew/bin/tmux -L dashboard attach -t left",
    "terminal": "/opt/homebrew/bin/tmux -L dashboard attach -t terminal",
    "monitor": "/opt/homebrew/bin/tmux -L dashboard attach -t monitor",
}
REPLY = "DASHBOARD_NATIVE_V1\nwindow-id\ntab-id\nleft-id\ncenter-id\nmonitor-id\n"
STATE = {
    "window_id": "window-id", "tab_id": "tab-id",
    "terminals": {"left": "left-id", "terminal": "center-id", "monitor": "monitor-id"},
    "hidden": False,
}
V2_REPLY = ("DASHBOARD_NATIVE_V2\nwindow-id\ntab-id\nleft-id\ncenter-id\nmonitor-id\n"
            "tab-id\nleft-id\ninplace\n")
INPLACE_STATE = dict(STATE, origin={"tab_id": "tab-id", "terminal_id": "left-id"},
                     launch_mode="inplace")
TAB_STATE = dict(STATE, origin={"tab_id": "origin-tab", "terminal_id": "origin-terminal"},
                 launch_mode="tab")


class NativeTests(unittest.TestCase):
    def test_origin_launch_matches_unique_title_without_focus_or_directory_guess(self):
        script = native.build_launch_script("/tmp/project", COMMANDS, "dashboard-origin-unique")
        self.assertIn('if name of candidateTerminal is "dashboard-origin-unique"', script)
        self.assertIn("repeat 30 times", script)
        self.assertIn("delay 0.05", script)
        self.assertIn('if matchCount > 1 then error', script)
        self.assertLess(script.index("if matchCount is not 1"), script.index("set createdTerminalIds"))
        for forbidden in ("front window", "focused terminal", "working directory of", "new window",
                          "input text", "send key"):
            self.assertNotIn(forbidden, script)

    def test_origin_launch_reuses_single_surface_but_isolates_unrelated_splits(self):
        script = native.build_launch_script("/tmp/project", COMMANDS, "marker")
        self.assertIn("if (count of terminals of originTab) is 1", script)
        self.assertIn("set leftTerminal to originTerminal", script)
        self.assertIn("set dashboardTab to new tab in dashboardWindow with configuration", script)
        self.assertEqual(script.count("direction right with configuration"), 2)
        self.assertNotIn("close window", script)
        self.assertNotIn("close tab", script)
        self.assertNotIn("close originTerminal", script)
        self.assertIn("repeat with createdId in reverse of createdTerminalIds", script)
        self.assertIn("set end of createdTerminalIds to id of centerTerminal", script)
        self.assertIn("set end of createdTerminalIds to id of monitorTerminal", script)

    def test_origin_marker_is_quoted_and_cannot_inject_script(self):
        marker = 'marker"\nend tell\ntell application "Finder" to quit'
        with self.assertRaises(ValueError):
            native.build_launch_script("/tmp/project", COMMANDS, marker)
        script = native.build_launch_script("/tmp/project", COMMANDS, 'marker"quoted')
        self.assertIn('marker\\"quoted', script)
        self.assertEqual(script.count('\ntell application '), 1)

    def test_launch_uses_new_window_and_explicit_attach_commands(self):
        script = native.build_launch_script("/tmp/project with spaces", COMMANDS)
        self.assertEqual(script.count("new window with configuration"), 1)
        self.assertEqual(script.count("direction right with configuration"), 2)
        self.assertIn("focus centerTerminal", script)
        self.assertIn('perform action "equalize_splits" on centerTerminal', script)
        self.assertNotIn("front window", script)
        self.assertNotIn("input text", script)
        for command in COMMANDS.values():
            self.assertIn(command, script)

    def test_paths_and_commands_are_applescript_strings_not_statements(self):
        # Quotes/newlines/backslashes must remain data, including in Unix paths.
        dangerous = '"\nend tell\ntell application "Finder" to quit\n\\'
        commands = dict(COMMANDS, terminal=COMMANDS["terminal"] + dangerous)
        script = native.build_launch_script("/tmp/" + dangerous, commands)
        self.assertNotIn('\nend tell\ntell application "Finder"', script)
        self.assertIn('\\"\\nend tell\\ntell application \\"Finder\\"', script)
        self.assertEqual(script.count('\ntell application '), 1)

    def test_launch_failure_cleanup_only_targets_its_own_new_window(self):
        script = native.build_launch_script("/tmp/project", COMMANDS)
        self.assertIn("set dashboardWindow to missing value", script)
        self.assertIn("on error failureMessage number failureNumber", script)
        self.assertIn("close window dashboardWindow", script)
        self.assertIn("error failureMessage number failureNumber", script)
        self.assertNotIn("close all", script)
        self.assertNotIn("close window 1", script)

    def test_missing_commands_relative_paths_and_nul_are_rejected(self):
        for root, commands in [
            ("relative/project", COMMANDS),
            ("/tmp/project", {"left": "tmux"}),
            ("/tmp/project\x00", COMMANDS),
            ("/tmp/project", dict(COMMANDS, terminal="tmux\x00bad")),
        ]:
            with self.subTest(root=root, commands=commands):
                with self.assertRaises(ValueError):
                    native.build_launch_script(root, commands)

    def test_state_protocol_roundtrip(self):
        self.assertEqual(native.parse_state(REPLY), STATE)
        hidden = native.parse_state(REPLY.replace("center-id", "-"))
        self.assertTrue(hidden["hidden"])
        self.assertEqual(hidden["terminals"]["terminal"], "")

    def test_v2_protocol_retains_origin_and_hidden_center(self):
        self.assertEqual(native.parse_state(V2_REPLY), INPLACE_STATE)
        tab_reply = V2_REPLY.replace("\ntab-id\nleft-id\ninplace", "\norigin-tab\norigin-terminal\ntab")
        self.assertEqual(native.parse_state(tab_reply), TAB_STATE)
        hidden = native.parse_state(V2_REPLY.replace("center-id", "-"))
        self.assertTrue(hidden["hidden"])
        self.assertEqual(hidden["origin"], INPLACE_STATE["origin"])

    def test_v2_rejects_inconsistent_origin_or_launch_mode(self):
        for reply in (V2_REPLY.replace("inplace", "window"),
                      V2_REPLY.replace("\ntab-id\nleft-id\ninplace", "\nother-tab\nleft-id\ninplace"),
                      V2_REPLY.replace("\ntab-id\nleft-id\ninplace", "\ntab-id\nother-id\ninplace"),
                      V2_REPLY.replace("inplace", "tab"), V2_REPLY + "extra\n"):
            with self.subTest(reply=reply):
                with self.assertRaises(RuntimeError):
                    native.parse_state(reply)

    def test_inplace_leave_retains_origin_after_complete_ownership_preflight(self):
        script = native.build_leave_script(INPLACE_STATE, "/tmp/project")
        self.assertIn('if (exists terminal id targetId) and targetId is not "left-id"', script)
        self.assertIn('set returnTerminal to terminal id "left-id"', script)
        self.assertLess(script.index("dashboardIds does not contain targetId"),
                        script.index("close terminal id targetId"))
        self.assertLess(script.index("returnIds does not contain"), script.index("close terminal id targetId"))
        for forbidden in ("new tab", "new window", "close window", "close tab", "kill-session"):
            self.assertNotIn(forbidden, script)

    def test_tab_leave_restores_original_tab_and_does_not_close_unknown_surfaces(self):
        script = native.build_leave_script(TAB_STATE, "/tmp/project")
        self.assertIn('set returnTab to tab id "origin-tab" of returnWindow', script)
        self.assertIn('set returnTerminal to terminal id "origin-terminal"', script)
        self.assertIn('repeat with managedId in {"left-id", "center-id", "monitor-id"}', script)
        self.assertNotIn("close tab", script)
        self.assertNotIn("close window", script)
        self.assertNotIn("close every", script)
        self.assertNotIn("new tab", script)

    def test_legacy_leave_creates_normal_shell_in_same_window_before_closing(self):
        script = native.build_leave_script(STATE, "/tmp/project")
        self.assertIn('new tab in dashboardWindow with configuration {initial working directory:"/tmp/project"', script)
        self.assertLess(script.index("new tab in dashboardWindow"), script.index("close terminal id targetId"))
        self.assertNotRegex(script, r"[{,]\s*command:")
        self.assertNotIn("new window", script)
        self.assertNotIn("close window", script)

    def test_retire_preflights_all_ids_before_close_and_tolerates_missing_ids(self):
        script = native.build_retire_script(STATE)
        self.assertIn("if exists terminal id targetId then", script)
        self.assertIn("dashboardIds does not contain targetId", script)
        self.assertLess(script.index("set end of existingManagedIds"), script.index("close terminal id targetId"))
        self.assertIn("repeat with managedId in reverse of existingManagedIds", script)
        for forbidden in ("new tab", "new window", "close tab", "close window", "focus "):
            self.assertNotIn(forbidden, script)
        hidden = native.parse_state(REPLY.replace("center-id", "-"))
        self.assertNotIn('terminal id "-"', native.build_retire_script(hidden))

    def test_saved_tab_fallback_handles_changed_window_identity_in_every_operation(self):
        scripts = {
            "focus": native.build_focus_script(STATE),
            "alive": native.build_focus_script(STATE, activate=False),
            "toggle": native.build_toggle_script(STATE, COMMANDS["terminal"], "/tmp/project"),
            "leave": native.build_leave_script(INPLACE_STATE, "/tmp/project"),
            "retire": native.build_retire_script(STATE),
        }
        for operation, script in scripts.items():
            with self.subTest(operation=operation):
                fallback = script.index("if dashboardTab is missing value then")
                exact_match = script.index('if id of candidateTab is "tab-id" then')
                membership = script.index("set dashboardIds to id of every terminal of dashboardTab")
                self.assertLess(fallback, exact_match)
                self.assertLess(exact_match, membership)
                self.assertIn("set dashboardWindow to contents of candidateWindow", script)
                self.assertIn("if matchingTabCount > 1 then", script)
                self.assertNotIn("front window", script)
                self.assertNotIn("working directory of", script)
                self.assertNotIn('if not (exists window id "window-id") then', script)

    def test_origin_tab_fallback_is_independent_and_validated_before_closing(self):
        script = native.build_leave_script(TAB_STATE, "/tmp/project")
        origin_fallback = script.index("if returnTab is missing value then")
        exact_origin = script.index('if id of candidateTab is "origin-tab" then')
        membership = script.index('if returnIds does not contain "origin-terminal"')
        first_close = script.index("close terminal id targetId")
        self.assertLess(origin_fallback, exact_origin)
        self.assertLess(exact_origin, membership)
        self.assertLess(membership, first_close)
        self.assertIn("set returnWindow to contents of candidateWindow", script)
        self.assertIn("set returnTab to contents of candidateTab", script)
        self.assertEqual(script.count("if matchingTabCount > 1 then"), 2)

    def test_fallback_still_rejects_a_surface_moved_out_of_the_recorded_tab(self):
        for script in (native.build_retire_script(STATE),
                       native.build_leave_script(TAB_STATE, "/tmp/project")):
            unique_match = script.index('if id of candidateTab is "tab-id" then')
            ownership = script.index("if dashboardIds does not contain targetId then")
            first_close = script.index("close terminal id targetId")
            self.assertLess(unique_match, ownership)
            self.assertLess(ownership, first_close)
            self.assertIn('error "A dashboard terminal was moved to another tab.', script)

    @patch("dashboard.native._execute")
    def test_leave_and_retire_validate_completion_without_retry(self, execute):
        execute.return_value = "DASHBOARD_NATIVE_LEFT\n"
        self.assertIsNone(native.leave(INPLACE_STATE, "/tmp/project"))
        execute.return_value = "DASHBOARD_NATIVE_RETIRED\n"
        self.assertIsNone(native.retire(STATE))
        execute.return_value = "incomplete"
        with self.assertRaisesRegex(RuntimeError, "incomplete leave"):
            native.leave(TAB_STATE, "/tmp/project")
        with self.assertRaisesRegex(RuntimeError, "incomplete retirement"):
            native.retire(STATE)
        self.assertEqual(execute.call_count, 4)

    @patch("dashboard.native._execute")
    def test_origin_launch_wrapper_passes_marker_and_parses_v2(self, execute):
        execute.return_value = V2_REPLY
        self.assertEqual(native.launch("/tmp/project", COMMANDS, origin_marker="unique-marker"), INPLACE_STATE)
        self.assertIn('if name of candidateTerminal is "unique-marker"', execute.call_args.args[0])

    def test_malformed_or_duplicate_state_is_rejected(self):
        for reply in ("", REPLY + "extra\n", REPLY.replace("center-id", "left-id"),
                      REPLY.replace("window-id", "-"), REPLY.replace("V1", "V2")):
            with self.subTest(reply=reply):
                with self.assertRaises(RuntimeError):
                    native.parse_state(reply)

    def test_toggle_targets_saved_tab_and_recreates_center_left_of_monitor(self):
        script = native.build_toggle_script(STATE, COMMANDS["terminal"], "/tmp/project")
        self.assertIn('window id "window-id"', script)
        self.assertIn('tab id "tab-id" of dashboardWindow', script)
        self.assertIn("dashboardIds does not contain centerId", script)
        self.assertIn("close terminal id centerId", script)
        self.assertIn("split monitorTerminal direction left", script)
        self.assertNotIn("kill-server", script)
        self.assertNotIn("kill-session", script)
        self.assertNotIn("close window", script)
        self.assertNotIn("front window", script)

    def test_hidden_state_compiles_a_reopen_path_without_fake_identifier(self):
        state = native.parse_state(REPLY.replace("center-id", "-"))
        script = native.build_toggle_script(state, COMMANDS["terminal"], "/tmp/project")
        self.assertIn('set centerId to ""', script)
        self.assertNotIn('terminal id "-"', script)

    @patch("dashboard.native.subprocess.run")
    def test_launch_submits_script_over_stdin_and_parses_state(self, run):
        run.return_value = subprocess.CompletedProcess(["osascript"], 0, REPLY, "")
        self.assertEqual(native.launch("/tmp/project", COMMANDS), STATE)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/osascript", "-"])
        self.assertIn("new window with configuration", kwargs["input"])
        self.assertNotIn("shell", kwargs)

    @patch("dashboard.native.subprocess.run")
    def test_native_error_does_not_return_success_state(self, run):
        run.return_value = subprocess.CompletedProcess(["osascript"], 1, "", "Not authorized (-1743)")
        with self.assertRaisesRegex(RuntimeError, "Not authorized"):
            native.launch("/tmp/project", COMMANDS)
        run.assert_called_once()

    @patch("dashboard.native.subprocess.run")
    def test_native_timeout_is_clear_and_not_retried(self, run):
        run.side_effect = subprocess.TimeoutExpired(["osascript"], 120)
        with self.assertRaisesRegex(RuntimeError, "permission prompt"):
            native.toggle(STATE, COMMANDS["terminal"], "/tmp/project")
        run.assert_called_once()

    def test_readonly_probe_contains_no_focus_or_surface_creation(self):
        script = native.build_focus_script(STATE, activate=False)
        self.assertIn("id of every terminal of dashboardTab", script)
        for action in ("activate window", "focus terminal", "new window", "split ", "close "):
            self.assertNotIn(action, script)

    @patch("dashboard.native.subprocess.run")
    def test_focus_missing_state_returns_false_without_recreating(self, run):
        run.return_value = subprocess.CompletedProcess(["osascript"], 0, "DASHBOARD_NATIVE_MISSING\n", "")
        self.assertFalse(native.focus(STATE))
        run.assert_called_once()
        self.assertNotIn("new window", run.call_args.kwargs["input"])

    @patch("dashboard.native.subprocess.run")
    def test_focus_existing_and_hidden_layouts(self, run):
        run.return_value = subprocess.CompletedProcess(["osascript"], 0, "DASHBOARD_NATIVE_ALIVE\n", "")
        self.assertTrue(native.focus_if_alive(STATE))
        hidden = native.parse_state(REPLY.replace("center-id", "-"))
        self.assertTrue(native.is_alive(hidden))
        self.assertNotIn("activate window", run.call_args.kwargs["input"])


if __name__ == "__main__":
    unittest.main()

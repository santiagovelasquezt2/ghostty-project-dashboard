"""Interactive tests for the two project panes (no terminal or repo required)."""

from __future__ import annotations

import copy
from datetime import datetime
import os
import unittest
from unittest.mock import patch

from rich.text import Text
from textual.widgets import Button, RichLog, Static

from dashboard.gitdata import Commit, FileChange, Snapshot
from dashboard.panels import BLUE, GREEN, MUTED, NEUTRAL, RED, TIMESTAMP, DashboardPanel, ProjectTree, commit_label, commit_timestamp, diff_text


def example_snapshot() -> Snapshot:
    return Snapshot(
        root="/tmp/example-project",
        branch="feature/login",
        base="main",
        base_sha="a" * 40,
        head="b" * 40,
        commits=[Commit("1234567" + "c" * 33, "Build [login] form"), Commit("7654321" + "d" * 33, "Fix validation")],
        committed_files=[
            FileChange("src/app/account/page.tsx", "M", 20, 4),
            FileChange("src/components/LoginButton.tsx", "A", 12, 0),
            FileChange("public/old-logo.svg", "D", 0, 6),
        ],
        all_files=[
            FileChange("src/app/account/page.tsx", "M", 28, 5, uncommitted=True),
            FileChange("src/components/LoginButton.tsx", "A", 12, 0),
            FileChange("public/old-logo.svg", "D", 0, 6),
            FileChange("src/new[1].ts", "A", 7, 0, uncommitted=True),
        ],
    )


class PanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Match the launcher's color-enabled environment, including under Codex.
        self.environment = patch.dict(os.environ)
        self.environment.start()
        os.environ.pop("NO_COLOR", None)
        self.addCleanup(self.environment.stop)

    async def test_tree_shows_neutral_ancestry_and_colored_changed_leaves(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        async with app.run_test(size=(44, 22)) as pilot:
            app.apply_snapshot(example_snapshot())
            await pilot.pause()
            self.assertEqual(app.entry_nodes["src/app/account"].label.plain, "account/")
            self.assertEqual(app.entry_nodes["src/app/account"].label.style, NEUTRAL)
            self.assertEqual(app.entry_nodes["src/app/account/page.tsx"].label.style, BLUE)
            self.assertEqual(app.entry_nodes["src/components/LoginButton.tsx"].label.style, GREEN)
            self.assertEqual(app.entry_nodes["public/old-logo.svg"].label.style, RED)
            self.assertEqual(app.entry_nodes["src/new[1].ts"].label.plain, "new[1].ts *")
            self.assertIn("example-project/", app.entry_nodes["/"].label.plain)
            tree = app.query_one(ProjectTree)
            segments = [segment for strip in tree.render_lines(tree.region.reset_offset) for segment in strip]
            self.assertEqual(next(segment for segment in segments if segment.text == "account/").style.color.get_truecolor().hex, NEUTRAL)
            self.assertEqual(next(segment for segment in segments if segment.text == "page.tsx").style.color.get_truecolor().hex, BLUE)
            self.assertIsNone(app._exception)

    async def test_clickable_modes_switch_both_tree_and_highlighted_totals(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        async with app.run_test(size=(44, 22)) as pilot:
            app.apply_snapshot(example_snapshot())
            await pilot.pause()
            totals = app.query_one("#totals", Static).render()
            self.assertIn("+47", str(totals))
            self.assertIn("−11", str(totals))
            await pilot.click("#committed")
            await pilot.pause()
            self.assertTrue(app.committed)
            self.assertNotIn("src/new[1].ts", app.entry_nodes)
            self.assertIn("+32", str(app.query_one("#totals", Static).render()))
            self.assertIn("−10", str(app.query_one("#totals", Static).render()))
            self.assertEqual(app.entry_nodes["src/components/LoginButton.tsx"].label.style, GREEN)
            self.assertTrue(app.query_one("#committed", Button).has_class("selected"))
            await pilot.press("a")
            self.assertFalse(app.committed)
            self.assertIn("src/new[1].ts", app.entry_nodes)

    async def test_unchanged_refresh_keeps_collapsed_tree_and_scroll(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        async with app.run_test(size=(44, 14)) as pilot:
            snap = example_snapshot()
            app.apply_snapshot(snap)
            await pilot.pause()
            folder = app.entry_nodes["src"]
            folder.collapse()
            app.query_one(ProjectTree).move_cursor(folder)
            await pilot.pause()
            app.apply_snapshot(copy.deepcopy(snap))
            await pilot.pause()
            self.assertIs(folder, app.entry_nodes["src"])
            self.assertFalse(folder.is_expanded)
            self.assertIs(app.query_one(ProjectTree).cursor_node, folder)
            snap.all_files.append(FileChange("src/another.ts", "A", 1, 0))
            app.apply_snapshot(snap)
            await pilot.pause()
            self.assertFalse(app.entry_nodes["src"].is_expanded)
            self.assertEqual(app.query_one(ProjectTree).cursor_node.data.key, "src")

    async def test_large_file_map_scrolls_both_axes_and_keeps_position_on_refresh(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        snap = example_snapshot()
        snap.all_files = [
            FileChange(f"src/long_feature_directory/nested_module/file_{i:02}_with_a_long_name.ts", "M", 1, 1)
            for i in range(50)
        ]
        async with app.run_test(size=(32, 18)) as pilot:
            app.apply_snapshot(snap)
            await pilot.pause()
            tree = app.query_one(ProjectTree)
            tree.focus()
            self.assertTrue(tree.show_horizontal_scrollbar)
            self.assertTrue(tree.show_vertical_scrollbar)
            await pilot.press("pagedown", "pagedown", "shift+right", "shift+right")
            await pilot.wait_for_scheduled_animations()
            await pilot.pause()
            self.assertGreater(tree.scroll_y, 0)
            self.assertGreater(tree.scroll_x, 0)
            position = (tree.scroll_x, tree.scroll_y)
            expanded = app.entry_nodes["src/long_feature_directory"].is_expanded
            changed = copy.deepcopy(snap)
            changed.all_files[-1].added = 2
            app.apply_snapshot(changed)
            await pilot.pause()
            self.assertEqual((tree.scroll_x, tree.scroll_y), position)
            self.assertEqual(app.entry_nodes["src/long_feature_directory"].is_expanded, expanded)
            self.assertEqual(app.entry_nodes[snap.all_files[0].path].label.style, BLUE)
            await pilot.press("shift+left", "shift+left", "pageup", "pageup")
            await pilot.wait_for_scheduled_animations()
            await pilot.pause()
            self.assertEqual(tree.scroll_x, 0)
            self.assertLess(tree.scroll_y, position[1])

    async def test_enter_diff_and_escape_returns_to_same_file(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        with patch("dashboard.panels.gitdata.file_diff", return_value="@@ -1 +1 @@\n-old\n+new [bold]\n") as diff:
            async with app.run_test(size=(44, 22)) as pilot:
                app.apply_snapshot(example_snapshot())
                await pilot.pause()
                app.query_one(ProjectTree).move_cursor(app.entry_nodes["src/app/account/page.tsx"])
                app.query_one(ProjectTree).focus()
                await pilot.press("enter")
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertTrue(app._preview_open)
                self.assertTrue(app.query_one("#preview", RichLog).display)
                self.assertFalse(app.query_one("#content").display)
                self.assertIn("+new [bold]", "".join(line.text for line in app.query_one(RichLog).lines))
                self.assertFalse(diff.call_args.kwargs["committed"])
                await pilot.click("#format-preview")
                await app.workers.wait_for_complete()
                self.assertFalse(app.formatted_preview)
                self.assertEqual(str(app.query_one("#format-preview", Button).label), "Original")
                self.assertNotIn("formatting unavailable", "".join(line.text for line in app.query_one(RichLog).lines))
                await pilot.press("b")
                self.assertFalse(app._preview_open)
                self.assertTrue(app.query_one("#content").display)
                self.assertEqual(app.query_one(ProjectTree).cursor_node.data.key, "src/app/account/page.tsx")

    async def test_committed_preview_uses_committed_snapshot(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        with patch("dashboard.panels.gitdata.file_diff", return_value="+committed\n") as diff:
            async with app.run_test(size=(44, 22)) as pilot:
                app.apply_snapshot(example_snapshot())
                await pilot.press("c")
                app.query_one(ProjectTree).move_cursor(app.entry_nodes["src/app/account/page.tsx"])
                await pilot.press("enter")
                await app.workers.wait_for_complete()
                self.assertTrue(diff.call_args.kwargs["committed"])
                self.assertEqual(diff.call_args.kwargs["head"], "b" * 40)

    async def test_commit_history_count_and_preview_are_literal(self) -> None:
        app = DashboardPanel("commits", "/tmp/example-project", auto_refresh=False)
        with patch("dashboard.panels.gitdata.commit_diff", return_value="+hello\n") as diff:
            async with app.run_test(size=(44, 13)) as pilot:
                snap = example_snapshot()
                app.apply_snapshot(snap)
                await pilot.pause()
                self.assertIn("2 ahead", str(app.query_one("#heading", Static).render()))
                self.assertFalse(app.query_one("#modes").display)
                self.assertIn("Build [login] form", app.entry_nodes[snap.commits[0].sha].label.plain)
                app.query_one(ProjectTree).focus()
                await pilot.press("enter")
                await app.workers.wait_for_complete()
                self.assertTrue(app._preview_open)
                diff.assert_called_once_with(snap.root, snap.commits[0].sha)

    async def test_footer_clicks_open_selected_file_back_and_refresh_current_diff(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        refreshed = example_snapshot()
        refreshed.head = "e" * 40
        with patch("dashboard.panels.gitdata.file_diff", side_effect=["+before refresh\n", "+after refresh\n"]) as diff, \
                patch("dashboard.panels.gitdata.snapshot", return_value=refreshed) as snapshot:
            async with app.run_test(size=(44, 22)) as pilot:
                app.apply_snapshot(example_snapshot())
                await pilot.pause()
                tree = app.query_one(ProjectTree)
                selected = "src/app/account/page.tsx"
                tree.move_cursor(app.entry_nodes[selected])
                tree.focus()
                await pilot.pause()
                await pilot.click("#open-entry")
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertTrue(app._preview_open)
                self.assertEqual(str(app.query_one("#open-entry", Button).label), "Back")
                self.assertIs(app.focused, app.query_one(RichLog))
                self.assertEqual(diff.call_args.args[2], selected)

                await pilot.click("#refresh")
                await app.workers.wait_for_complete()
                await pilot.pause()
                snapshot.assert_called_once_with(app.project_root, None)
                self.assertEqual(diff.call_count, 2)
                self.assertEqual(diff.call_args.kwargs["head"], refreshed.head)
                self.assertIn("+after refresh", "".join(line.text for line in app.query_one(RichLog).lines))
                self.assertIs(app.focused, app.query_one(RichLog))

                await pilot.click("#open-entry")
                await pilot.pause()
                self.assertFalse(app._preview_open)
                self.assertEqual(str(app.query_one("#open-entry", Button).label), "Open")
                self.assertIs(app.focused, tree)
                self.assertEqual(tree.cursor_node.data.key, selected)

    async def test_commit_footer_opens_selection_and_refresh_updates_list(self) -> None:
        app = DashboardPanel("commits", "/tmp/example-project", auto_refresh=False)
        snap = example_snapshot()
        refreshed = copy.deepcopy(snap)
        refreshed.commits.append(Commit("f" * 40, "Another commit"))
        with patch("dashboard.panels.gitdata.commit_diff", return_value="+selected commit\n") as diff, \
                patch("dashboard.panels.gitdata.snapshot", return_value=refreshed):
            async with app.run_test(size=(30, 14)) as pilot:
                app.apply_snapshot(snap)
                await pilot.pause()
                tree = app.query_one(ProjectTree)
                tree.focus()
                await pilot.press("down")
                chosen = snap.commits[1].sha
                self.assertEqual(tree.cursor_node.data.key, chosen)
                await pilot.click("#open-entry")
                await app.workers.wait_for_complete()
                self.assertTrue(app._preview_open)
                diff.assert_called_once_with(snap.root, chosen)
                await pilot.click("#open-entry")
                await pilot.pause()
                await pilot.click("#refresh")
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertIn("3 ahead", str(app.query_one("#heading", Static).render()))
                self.assertEqual(tree.cursor_node.data.key, chosen)
                self.assertIs(app.focused, tree)

    async def test_clickable_footer_fits_and_opens_at_22_columns(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        with patch("dashboard.panels.gitdata.file_diff", return_value="+narrow preview\n"):
            async with app.run_test(size=(22, 12)) as pilot:
                app.apply_snapshot(example_snapshot())
                await pilot.pause()
                tree = app.query_one(ProjectTree)
                tree.move_cursor(app.entry_nodes["public/old-logo.svg"])
                await pilot.pause()
                for selector in ("#open-entry", "#refresh"):
                    button = app.query_one(selector, Button)
                    self.assertEqual(button.region.intersection(app.screen.region), button.region)
                    self.assertEqual(button.region.height, 1)
                    self.assertGreater(button.region.width, 0)
                await pilot.click("#open-entry")
                await app.workers.wait_for_complete()
                self.assertTrue(app._preview_open)
                await pilot.click("#open-entry")
                self.assertFalse(app._preview_open)
                await pilot.resize_terminal(56, 24)
                self.assertIsNone(app._exception)

    async def test_narrow_pane_modes_still_accessible_and_resize_cleanly(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        async with app.run_test(size=(22, 12)) as pilot:
            app.apply_snapshot(example_snapshot())
            await pilot.pause()
            self.assertEqual(str(app.query_one("#all", Button).label), "All")
            self.assertLessEqual(app.query_one("#committed").region.right, 22)
            await pilot.click("#committed")
            self.assertTrue(app.committed)
            await pilot.resize_terminal(56, 24)
            self.assertEqual(str(app.query_one("#all", Button).label), "All changes")
            self.assertIsNone(app._exception)

    async def test_empty_and_error_states(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        async with app.run_test(size=(30, 12)) as pilot:
            snap = Snapshot("/tmp/example-project", "main", "main", "a", "a")
            app.apply_snapshot(snap)
            await pilot.pause()
            self.assertTrue(app.query_one("#empty").display)
            self.assertTrue(app.query_one("#open-entry", Button).disabled)
            self.assertFalse(app.query_one("#refresh", Button).disabled)
            self.assertIn("No changes", str(app.query_one("#empty", Static).render()))
            snap.error = "No base [main] found"
            snap.all_files = [FileChange("partial.ts", "A", 50, 0)]
            app.apply_snapshot(snap)
            self.assertIn("No base [main] found", str(app.query_one("#subtitle", Static).render()))
            self.assertIn("Totals unavailable", str(app.query_one("#totals", Static).render()))
            self.assertNotIn("+50", str(app.query_one("#totals", Static).render()))

    async def test_background_refresh_does_not_close_or_refocus_preview(self) -> None:
        app = DashboardPanel("files", "/tmp/example-project", auto_refresh=False)
        with patch("dashboard.panels.gitdata.file_diff", return_value="+preview\n"):
            async with app.run_test(size=(44, 22)) as pilot:
                snap = example_snapshot()
                app.apply_snapshot(snap)
                await pilot.pause()
                app.query_one(ProjectTree).move_cursor(app.entry_nodes["src/app/account/page.tsx"])
                app.query_one(ProjectTree).focus()
                await pilot.press("enter")
                await app.workers.wait_for_complete()
                snap = copy.deepcopy(snap)
                snap.all_files.append(FileChange("other.txt", "A", 1, 0))
                app.apply_snapshot(snap)
                await pilot.pause()
                self.assertTrue(app._preview_open)
                self.assertIs(app.focused, app.query_one(RichLog))
                self.assertFalse(app.query_one(ProjectTree).display)


class RenderingTests(unittest.TestCase):
    def test_local_commit_time_uses_faded_am_pm_without_an_extra_line(self):
        local = datetime.now().astimezone()
        for hour, expected in ((0, "12:05 AM"), (12, "12:05 PM"), (23, "11:05 PM")):
            value = local.replace(hour=hour, minute=5).isoformat()
            self.assertTrue(commit_timestamp(value).endswith(expected))
            label = commit_label(Commit("abcdef123456", "A compact commit message", value), 55)
            self.assertEqual(label.cell_len, 55)
            self.assertTrue(label.plain.endswith(expected))
            self.assertNotIn("\n", label.plain)
            self.assertEqual(label.spans[-1].style.color.get_truecolor().hex, TIMESTAMP)

    def test_diff_colors_and_control_sanitizing(self) -> None:
        rendered = diff_text("@@ x @@\n-old\n+new [red]\n\x1b]52;clipboard\x07\n")
        self.assertIn("[red]", rendered.plain)
        self.assertNotIn("\x1b", rendered.plain)
        styles = {str(span.style) for span in rendered.spans}
        self.assertTrue({RED, GREEN, BLUE}.issubset(styles))


if __name__ == "__main__":
    unittest.main()

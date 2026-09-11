"""Process monitor interaction, resource sorting, and honest missing-data states."""

import copy
import os
import unittest
from unittest.mock import patch

from textual.widgets import Button, DataTable, Input, Static

from dashboard.processdata import Process, Snapshot
from dashboard.processes import ProcessMonitor


def sample() -> Snapshot:
    return Snapshot(
        processes=[
            Process(100, "Grok", 142.5, 350 * 1024**2),
            Process(200, "Google Chrome", 12, 900 * 1024**2),
            Process(300, "Ghostty [terminal]", 2.5, 85 * 1024**2),
        ],
        cpu_percent=24.5,
        memory_used=8 * 1024**3,
        memory_total=24 * 1024**3,
    )


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ)
        environment.start()
        os.environ.pop("NO_COLOR", None)
        self.addCleanup(environment.stop)

    async def test_initial_gpu_view_is_selected_without_sending_keys(self) -> None:
        app = ProcessMonitor(auto_refresh=False, initial_view="gpu")
        async with app.run_test(size=(48, 25)) as pilot:
            snap = sample()
            snap.gpu_available = True
            snap.processes[1].gpu = 12
            snap.processes[1].gpu_time_ns = 2_000_000_000
            app.apply_snapshot(snap)
            await pilot.pause()
            self.assertEqual(app.metric_name, "gpu")
            self.assertTrue(app.query_one("#gpu", Button).has_class("selected"))
            self.assertFalse(app.query_one("#cpu", Button).has_class("selected"))
            self.assertEqual(app.query_one(DataTable).get_row_at(0)[0].plain, "Google Chrome")

    def test_initial_view_rejects_unknown_names(self) -> None:
        with self.assertRaises(ValueError):
            ProcessMonitor(auto_refresh=False, initial_view="imaginary")

    async def test_cpu_summary_and_resource_tabs_sort_real_processes(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 25)) as pilot:
            app.apply_snapshot(sample())
            await pilot.pause()
            table = app.query_one(DataTable)
            self.assertEqual(table.get_row_at(0)[0].plain, "Grok")
            self.assertIn("142.5", [cell.plain for cell in table.get_row_at(0)])
            self.assertIn("24.5%", str(app.query_one("#cpu-value", Static).render()))
            self.assertIn("8.0G / 24.0G", str(app.query_one("#memory-value", Static).render()))
            self.assertIn("100% CPU = one core", str(app.query_one("#note", Static).render()))
            await pilot.click("#memory")
            self.assertEqual(app.metric_name, "memory")
            self.assertEqual(table.get_row_at(0)[0].plain, "Google Chrome")
            self.assertTrue(app.query_one("#memory", Button).has_class("selected"))
            await pilot.press("c")
            self.assertEqual(table.get_row_at(0)[0].plain, "Grok")

    async def test_gpu_missing_data_has_no_fake_table(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(40, 20)) as pilot:
            snap = sample()
            snap.gpu_note = ""
            app.apply_snapshot(snap)
            await pilot.click("#gpu")
            self.assertFalse(app.query_one(DataTable).display)
            self.assertTrue(app.query_one("#empty").display)
            self.assertIn("Per-process GPU usage is unavailable", str(app.query_one("#empty", Static).render()))
            self.assertIn("hardware or macOS version", str(app.query_one("#empty", Static).render()))
            await pilot.press("m")
            self.assertTrue(app.query_one(DataTable).display)

    async def test_gpu_only_uses_available_gpu_values(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 25)) as pilot:
            snap = sample()
            snap.gpu_available = True
            snap.processes[1].gpu = 44.0
            snap.processes[2].gpu = 2.0
            app.apply_snapshot(snap)
            await pilot.press("g")
            table = app.query_one(DataTable)
            self.assertEqual(table.row_count, 2)
            self.assertEqual(table.get_row_at(0)[0].plain, "Google Chrome")
            self.assertNotIn("Grok", [table.get_row_at(i)[0].plain for i in range(table.row_count)])

    async def test_gpu_sampling_shows_detected_clients_with_unknown_rates(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 25)) as pilot:
            snap = sample()
            snap.gpu_available = True
            snap.gpu_sampling = True
            snap.processes[1].gpu_time_ns = 2_000_000_000
            snap.processes[2].gpu_time_ns = 5_000_000_000
            app.apply_snapshot(snap)
            await pilot.press("g")
            table = app.query_one(DataTable)
            self.assertEqual(table.row_count, 2)
            self.assertEqual(table.get_row_at(0)[0].plain, "Ghostty [terminal]")
            self.assertEqual(table.get_cell("300", "gpu").plain, "—")
            self.assertEqual(table.get_cell("300", "gpu_time").plain, "5.00s")
            self.assertIn("sampling GPU usage", str(app.query_one("#note", Static).render()))
            self.assertTrue(table.display)
            self.assertIn("Reported GPU time", str(app.query_one("#detail", Static).render()))

    async def test_gpu_rates_idle_clients_and_reported_time_updates_are_truthful(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 25)) as pilot:
            snap = sample()
            snap.gpu_available = True
            snap.processes[0].gpu = 150.5
            snap.processes[0].gpu_time_ns = 1_000_000_000
            snap.processes[1].gpu = 0.0
            snap.processes[1].gpu_time_ns = 9_000_000_000
            snap.processes[2].gpu_time_ns = 20_000_000_000
            app.apply_snapshot(snap)
            await pilot.press("g")
            table = app.query_one(DataTable)
            self.assertEqual([table.get_row_at(i)[0].plain for i in range(3)],
                             ["Grok", "Google Chrome", "Ghostty [terminal]"])
            self.assertEqual(table.get_cell("100", "gpu").plain, "150.5")
            self.assertEqual(table.get_cell("200", "gpu").plain, "0.0")
            self.assertIn("GPU time / elapsed time", str(app.query_one("#note", Static).render()))
            updated = copy.deepcopy(snap)
            updated.processes[0].gpu_time_ns = 500_000_000
            app.apply_snapshot(updated)
            self.assertEqual(table.get_cell("100", "gpu_time").plain, "500.0ms")
            self.assertEqual(app.selected_pid(), 100)

    async def test_gpu_filter_and_narrow_resize_keep_unknown_clients_and_pid(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(22, 20)) as pilot:
            snap = sample()
            snap.gpu_available = True
            snap.gpu_sampling = True
            snap.processes[2].gpu_time_ns = 0
            app.apply_snapshot(snap)
            await pilot.press("g")
            table = app.query_one(DataTable)
            self.assertEqual(table.row_count, 1)
            self.assertEqual([column.key.value for column in table.ordered_columns], ["name", "gpu"])
            self.assertEqual(table.get_cell("300", "gpu").plain, "—")
            self.assertIn("PID 300", str(app.query_one("#detail", Static).render()))
            await pilot.click("#filter-button")
            await pilot.press("g", "h", "o", "s", "t")
            self.assertEqual(table.row_count, 1)
            await pilot.resize_terminal(56, 25)
            self.assertIn("gpu_time", [column.key.value for column in table.ordered_columns])
            self.assertEqual(app.selected_pid(), 300)
            self.assertIs(app.focused, app.query_one(Input))

    async def test_refresh_preserves_selected_pid_and_scroll(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 20)) as pilot:
            snap = sample()
            snap.processes.extend(Process(i, f"process-{i}", 0, 1000) for i in range(400, 500))
            app.apply_snapshot(snap)
            await pilot.pause()
            table = app.query_one(DataTable)
            table.move_cursor(row=20)
            await pilot.pause()
            pid = app.selected_pid()
            scroll_y = table.scroll_y
            snap = copy.deepcopy(snap)
            next(item for item in snap.processes if item.pid == pid).cpu = 300
            app.apply_snapshot(snap)
            await pilot.pause()
            self.assertEqual(app.selected_pid(), pid)
            self.assertEqual(table.scroll_y, scroll_y)
            app.apply_snapshot(copy.deepcopy(snap))
            self.assertEqual(table.scroll_y, scroll_y)

    async def test_filter_is_literal_case_insensitive_and_preserves_focus_on_refresh(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 24)) as pilot:
            app.apply_snapshot(sample())
            await pilot.press("slash")
            field = app.query_one(Input)
            self.assertIs(app.focused, field)
            await pilot.press("g", "h", "o", "s", "t")
            self.assertEqual(app.query_one(DataTable).row_count, 1)
            self.assertEqual(app.query_one(DataTable).get_row_at(0)[0].plain, "Ghostty [terminal]")
            app.apply_snapshot(sample())
            self.assertIs(app.focused, field)
            await pilot.press("escape")
            self.assertFalse(field.display)
            self.assertEqual(app.query_one(DataTable).row_count, 3)

    async def test_narrow_resize_preserves_name_metric_and_pid_detail(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(22, 16)) as pilot:
            app.apply_snapshot(sample())
            await pilot.pause()
            table = app.query_one(DataTable)
            self.assertEqual(len(table.columns), 2)
            self.assertEqual([column.key.value for column in table.ordered_columns], ["name", "cpu"])
            self.assertLessEqual(app.query_one("#memory").region.right, 22)
            self.assertIn("PID 100", str(app.query_one("#detail", Static).render()))
            await pilot.resize_terminal(56, 24)
            self.assertEqual(len(table.columns), 4)
            self.assertEqual(app.selected_pid(), 100)
            self.assertIsNone(app._exception)

    async def test_first_sample_and_failed_system_counters_do_not_show_invented_values(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(40, 20)) as pilot:
            snap = sample()
            snap.cpu_percent = None
            snap.memory_used = None
            snap.memory_total = None
            app.apply_snapshot(snap)
            await pilot.pause()
            self.assertIn("sampling", str(app.query_one("#cpu-value", Static).render()))
            self.assertIn("— / —", str(app.query_one("#memory-value", Static).render()))
            self.assertIsNone(app._exception)

    async def test_first_measured_cpu_sample_starts_at_highest_consumer(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 20)) as pilot:
            first = sample()
            first.cpu_percent = None
            for item in first.processes:
                item.cpu = 0
            first.processes.extend(Process(i, f"idle-{i}", 0, 1000) for i in range(500, 600))
            app.apply_snapshot(first)
            await pilot.pause()
            measured = copy.deepcopy(first)
            measured.cpu_percent = 25
            measured.processes[-1].cpu = 200
            app.apply_snapshot(measured)
            await pilot.pause()
            table = app.query_one(DataTable)
            self.assertEqual(app.selected_pid(), 599)
            self.assertEqual(table.cursor_row, 0)
            self.assertEqual(table.scroll_y, 0)

    async def test_footer_filter_and_clear_are_clickable(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 24)) as pilot:
            app.apply_snapshot(sample())
            await pilot.click("#filter-button")
            field = app.query_one(Input)
            self.assertTrue(field.display)
            self.assertIs(app.focused, field)
            await pilot.press("g", "h", "o", "s", "t")
            self.assertEqual(app.query_one(DataTable).row_count, 1)
            self.assertEqual(str(app.query_one("#filter-button", Button).label), "Clear")
            await pilot.click("#filter-button")
            self.assertFalse(field.display)
            self.assertEqual(app.query_one(DataTable).row_count, 3)
            self.assertEqual(str(app.query_one("#filter-button", Button).label), "Filter")

    async def test_footer_scroll_and_refresh_keep_selection_and_focus(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        snap = sample()
        snap.processes.extend(Process(i, f"process-{i}", 0, 1000) for i in range(400, 500))
        with patch("dashboard.processes.processdata.collect", return_value=copy.deepcopy(snap)) as collect:
            async with app.run_test(size=(48, 20)) as pilot:
                app.apply_snapshot(snap)
                await pilot.pause()
                table = app.query_one(DataTable)
                table.focus()
                pid = app.selected_pid()
                await pilot.click("#scroll-down")
                self.assertGreater(table.scroll_y, 0)
                self.assertEqual(app.selected_pid(), pid)
                self.assertIs(app.focused, table)
                after_down = table.scroll_y
                await pilot.click("#refresh-button")
                await app.workers.wait_for_complete()
                await pilot.pause()
                collect.assert_called_once()
                self.assertEqual(app.selected_pid(), pid)
                self.assertEqual(table.scroll_y, after_down)
                self.assertIs(app.focused, table)
                await pilot.click("#scroll-up")
                self.assertLess(table.scroll_y, after_down)
                self.assertEqual(app.selected_pid(), pid)

    async def test_footer_wraps_at_22_columns_and_every_button_is_clickable(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        with patch("dashboard.processes.processdata.collect", return_value=sample()):
            async with app.run_test(size=(22, 20)) as pilot:
                app.apply_snapshot(sample())
                await pilot.pause()
                footer = app.query_one("#actions")
                self.assertEqual(footer.size.height, 2)
                for button_id in ("filter-button", "refresh-button", "scroll-up", "scroll-down"):
                    button = app.query_one("#" + button_id, Button)
                    self.assertLessEqual(button.region.right, 22)
                    self.assertGreaterEqual(button.region.x, 0)
                    self.assertTrue(await pilot.click("#" + button_id))
                await app.workers.wait_for_complete()
                await pilot.resize_terminal(48, 24)
                self.assertEqual(footer.size.height, 1)
                self.assertIsNone(app._exception)

    async def test_footer_accepts_rapid_filter_clear_and_repeated_scroll_clicks(self) -> None:
        app = ProcessMonitor(auto_refresh=False)
        async with app.run_test(size=(48, 20)) as pilot:
            snap = sample()
            snap.processes.extend(Process(i, f"process-{i}", 0, 1000) for i in range(400, 500))
            app.apply_snapshot(snap)
            await pilot.pause()
            field = app.query_one(Input)
            for _ in range(3):
                await pilot.click("#filter-button")
                self.assertTrue(field.display)
                await pilot.click("#filter-button")
                self.assertFalse(field.display)
            table = app.query_one(DataTable)
            pid = app.selected_pid()
            previous_scroll = table.scroll_y
            for _ in range(4):
                await pilot.click("#scroll-down")
                self.assertGreater(table.scroll_y, previous_scroll)
                previous_scroll = table.scroll_y
            self.assertEqual(app.selected_pid(), pid)
            self.assertIs(app.focused, table)


if __name__ == "__main__":
    unittest.main()

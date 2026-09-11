import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dashboard import processdata as data


class ProcessDataTests(unittest.TestCase):
    def setUp(self):
        self.patchers = [patch.object(data, "_PREVIOUS_PROCESSES", {}),
                         patch.object(data, "_PREVIOUS_TIME", None),
                         patch.object(data, "_PREVIOUS_CPU_TICKS", None)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_interval_cpu_pid_reuse_and_exited_processes(self):
        start = "Thu Sep 10 22:00:00 2026"
        first = data._sample_processes([(12, start, 100, 4096, "app"),
                                        (14, start, 90, 8192, "exiting")], 10)
        self.assertEqual(first[0].cpu, 0)
        second = data._sample_processes([(12, start, 101.5, 4096, "app")], 12)
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].cpu, 75)
        reused = data._sample_processes([(12, "new start", 200, 4096, "new app")], 14)
        self.assertEqual(reused[0].cpu, 0)

    def test_cpu_time_formats_and_names(self):
        self.assertEqual(data._cpu_seconds("2-01:02:03.25"), 176523.25)
        self.assertEqual(data._cpu_seconds("84:15.58"), 5055.58)
        rows = data._process_rows("12 0:02.50 128 Thu Sep 10 22:00:00 2026 /Apps/Test App.app/Contents/MacOS/Test App\nmalformed\n")
        self.assertEqual(rows[0][4], "Test App")
        self.assertEqual(rows[0][3], 131072)
        self.assertNotIn("\x1b", data._short_name("/tmp/app\x1b[2J"))

    def test_system_cpu_wrap_and_memory_pages(self):
        self.assertIsNone(data._system_cpu((0xFFFFFFFE, 20, 30, 0)))
        # 4 user ticks across wrap + 2 system + 4 idle = 60% busy.
        self.assertAlmostEqual(data._system_cpu((2, 22, 34, 0)), 60)
        raw = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
               "Pages free: 10.\nPages inactive: 20.\nPages speculative: 5.\n")
        self.assertEqual(data._memory_used(raw, 100 * 16384), 65 * 16384)
        self.assertIsNone(data._memory_used("invalid", 1024))

    def test_cpu_is_not_used_as_a_fake_gpu_value(self):
        process = data.Process(12, "App", 35, 4096)
        state = data.Snapshot([process], 30, 1024, 2048)
        self.assertIsNone(process.gpu)
        self.assertFalse(state.gpu_available)
        self.assertEqual(state.gpu_note, data.GPU_NOTE)

    def test_gpu_measurements_join_processes_by_pid_and_creation_identity(self):
        from dashboard import gpudata
        rows = ("12 0:02.50 128 Thu Sep 10 22:00:00 2026 /Apps/Metal App\n"
                "13 0:01.00 64 Thu Sep 10 22:00:01 2026 /Apps/Plain App\n")
        gpu = SimpleNamespace(rates={12: 17.5, 999: 80.0},
                              cumulative_ns={12: 9_000_000_000, 999: 1},
                              available=True, sampling=False, note="Measured GPU activity")
        with patch.object(data, "_run", return_value=rows), \
                patch.object(data, "_memory_total", return_value=1024), \
                patch.object(data, "_memory_used", return_value=512), \
                patch.object(data, "_cpu_ticks", return_value=(1, 2, 3, 0)), \
                patch.object(gpudata, "sample", return_value=gpu) as sample:
            snapshot = data.collect()
        sample.assert_called_once_with({12: "Thu Sep 10 22:00:00 2026", 13: "Thu Sep 10 22:00:01 2026"})
        self.assertEqual([p.pid for p in snapshot.processes], [12, 13])
        self.assertEqual(snapshot.processes[0].gpu, 17.5)
        self.assertEqual(snapshot.processes[0].gpu_time_ns, 9_000_000_000)
        self.assertIsNone(snapshot.processes[1].gpu)
        self.assertIsNone(snapshot.processes[1].gpu_time_ns)
        self.assertTrue(snapshot.gpu_available)
        self.assertFalse(snapshot.gpu_sampling)


if __name__ == "__main__":
    unittest.main()

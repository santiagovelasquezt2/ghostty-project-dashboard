import unittest
import subprocess
from unittest.mock import patch

from dashboard import gpudata as gpu


def client(pid, registry_id, *counters):
    return {"IOUserClientCreator": f"pid {pid}, Test App", "IORegistryEntryID": registry_id,
            "AppUsage": [{"API": "Metal", "accumulatedGPUTime": count} for count in counters]}


class GPUDataTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("_PREVIOUS", {}), ("_PREVIOUS_TIME", None)):
            patcher = patch.object(gpu, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read(self, timestamp, nodes, identities=None):
        with patch.object(gpu, "_read", return_value=(timestamp, nodes)):
            return gpu.sample(identities or {10: "start-10", 20: "start-20"})

    def test_real_nanosecond_deltas_sum_clients_and_are_not_clamped(self):
        first = self.read(1_000_000_000, [client(10, 1, 100_000_000), client(10, 2, 500_000_000)])
        self.assertTrue(first.available)
        self.assertTrue(first.sampling)
        self.assertIsNone(first.rates[10])
        second = self.read(3_000_000_000, [client(10, 1, 2_100_000_000), client(10, 2, 1_500_000_000)])
        self.assertEqual(second.rates[10], 150.0)
        self.assertEqual(second.cumulative_ns[10], 3_600_000_000)
        self.assertFalse(second.sampling)

    def test_disappearing_client_does_not_subtract_surviving_work(self):
        self.read(1_000_000_000, [client(10, 1, 100_000_000), client(10, 2, 90_000_000_000)])
        state = self.read(3_000_000_000, [client(10, 1, 200_000_000)])
        self.assertEqual(state.rates[10], 5.0)
        self.assertEqual(state.cumulative_ns[10], 200_000_000)
        self.assertTrue(state.sampling)

    def test_disappearing_client_does_not_report_false_idle(self):
        self.read(1_000_000_000, [client(10, 1, 100), client(10, 2, 900)])
        incomplete = self.read(3_000_000_000, [client(10, 1, 100)])
        self.assertIsNone(incomplete.rates[10])
        self.assertTrue(incomplete.sampling)
        stable = self.read(5_000_000_000, [client(10, 1, 100)])
        self.assertEqual(stable.rates[10], 0.0)
        self.assertFalse(stable.sampling)

    def test_new_client_preserves_known_positive_work_with_sampling_marker(self):
        self.read(1_000_000_000, [client(10, 1, 100_000_000)])
        state = self.read(3_000_000_000, [client(10, 1, 300_000_000), client(10, 2, 90_000_000_000)])
        self.assertEqual(state.rates[10], 10.0)
        self.assertTrue(state.sampling)

    def test_context_array_shrinking_does_not_report_false_idle(self):
        self.read(1_000_000_000, [client(10, 1, 100_000_000, 200_000_000)])
        # The remaining context gained 200ms, but the removed context's
        # lifetime total would otherwise cancel it out and look like idle.
        changed = self.read(2_000_000_000, [client(10, 1, 300_000_000)])
        self.assertIsNone(changed.rates[10])
        self.assertTrue(changed.sampling)
        stable = self.read(3_000_000_000, [client(10, 1, 500_000_000)])
        self.assertEqual(stable.rates[10], 20.0)
        self.assertFalse(stable.sampling)

    def test_context_array_expanding_does_not_report_a_spike(self):
        self.read(1_000_000_000, [client(10, 1, 100_000_000)])
        changed = self.read(2_000_000_000,
                            [client(10, 1, 100_000_000, 90_000_000_000)])
        self.assertIsNone(changed.rates[10])
        self.assertTrue(changed.sampling)
        stable = self.read(3_000_000_000,
                           [client(10, 1, 200_000_000, 90_000_000_000)])
        self.assertEqual(stable.rates[10], 10.0)
        self.assertFalse(stable.sampling)

    def test_context_shape_change_preserves_other_client_work(self):
        self.read(1_000_000_000, [client(10, 1, 100_000_000, 200_000_000),
                                 client(10, 2, 100_000_000)])
        changed = self.read(2_000_000_000, [client(10, 1, 300_000_000),
                                           client(10, 2, 250_000_000)])
        self.assertEqual(changed.rates[10], 15.0)
        self.assertTrue(changed.sampling)

    def test_api_label_change_establishes_a_new_baseline(self):
        self.read(1_000_000_000, [client(10, 1, 100_000_000)])
        node = client(10, 1, 900_000_000)
        node["AppUsage"][0]["API"] = "Other API"
        changed = self.read(2_000_000_000, [node])
        self.assertIsNone(changed.rates[10])
        self.assertTrue(changed.sampling)

    def test_counter_reset_and_pid_reuse_establish_new_baselines(self):
        self.read(1_000_000_000, [client(10, 1, 900_000_000)])
        reset = self.read(2_000_000_000, [client(10, 1, 100)])
        self.assertIsNone(reset.rates[10])
        reused = self.read(3_000_000_000, [client(10, 1, 900_000_000)], {10: "new-start"})
        self.assertIsNone(reused.rates[10])
        stable = self.read(4_000_000_000, [client(10, 1, 900_000_000)], {10: "new-start"})
        self.assertEqual(stable.rates[10], 0.0)
        self.assertFalse(stable.sampling)

    def test_failed_read_clears_old_baseline(self):
        self.read(1_000_000_000, [client(10, 1, 100)])
        with patch.object(gpu, "_read", side_effect=gpu.GPUUnavailable("Read failed")):
            failed = gpu.sample({10: "start-10"})
        self.assertFalse(failed.available)
        self.assertEqual(failed.rates, {})
        self.assertEqual(failed.note, "Read failed")
        recovered = self.read(3_000_000_000, [client(10, 1, 900_000_000)])
        self.assertIsNone(recovered.rates[10])

    def test_unavailable_unknown_pid_and_malformed_counters(self):
        self.assertFalse(self.read(1, []).available)
        state = self.read(2, [client(99, 1, 100), client(10, 2, -1),
                              {"AppUsage": [], "IOUserClientCreator": "unparseable", "IORegistryEntryID": 3}])
        self.assertTrue(state.available)
        self.assertEqual(state.rates, {})
        self.assertEqual(state.cumulative_ns, {})

    def test_only_malformed_counters_are_not_reported_as_supported(self):
        malformed = self.read(1, [client(10, 1, -1)])
        self.assertFalse(malformed.available)
        self.assertIn("unsupported format", malformed.note)
        absent_pid = self.read(2, [client(99, 1, 100)])
        self.assertTrue(absent_pid.available)
        self.assertEqual(absent_pid.rates, {})

    def test_missing_or_malformed_client_identities_are_unsupported(self):
        for field, value in (("IORegistryEntryID", None),
                             ("IORegistryEntryID", "1"),
                             ("IOUserClientCreator", None),
                             ("IOUserClientCreator", "unparseable")):
            with self.subTest(field=field, value=value):
                node = client(10, 1, 100)
                if value is None:
                    node.pop(field)
                else:
                    node[field] = value
                state = self.read(1, [node])
                self.assertFalse(state.available)
                self.assertIn("unsupported format", state.note)
                self.assertEqual(state.rates, {})

    def test_truncated_xml_and_oversize_output_are_unavailable(self):
        def malformed(*args, **kwargs):
            kwargs["stdout"].write(b'<?xml version="1.0"?><plist><array>')
            return subprocess.CompletedProcess(args[0], 0)

        with patch.object(gpu.subprocess, "run", side_effect=malformed):
            self.assertFalse(gpu.sample({10: "start-10"}).available)

        def oversized(*args, **kwargs):
            kwargs["stdout"].write(b"x" * 33)
            return subprocess.CompletedProcess(args[0], 0)

        with patch.object(gpu, "MAX_OUTPUT_BYTES", 32), \
                patch.object(gpu.subprocess, "run", side_effect=oversized):
            result = gpu.sample({10: "start-10"})
        self.assertFalse(result.available)
        self.assertIn("read limit", result.note)


if __name__ == "__main__":
    unittest.main()

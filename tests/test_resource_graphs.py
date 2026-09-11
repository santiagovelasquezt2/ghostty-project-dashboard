import unittest

from dashboard.resource_graphs import memory_readings


class MemoryReadingsTests(unittest.TestCase):
    def test_matches_btop_memory_definition(self):
        raw = '''Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 10.
Pages active: 200.
Pages inactive: 90.
Pages wired down: 100.
File-backed pages: 80.
'''
        self.assertEqual(memory_readings(raw), dict(used=300 * 16384, cached=80 * 16384, free=10 * 16384))

    def test_unavailable_readings_remain_unknown(self):
        self.assertEqual(memory_readings(None), dict(used=None, cached=None, free=None))
        self.assertIsNone(memory_readings('page size of 4096 bytes\nPages active: 10.')["used"])

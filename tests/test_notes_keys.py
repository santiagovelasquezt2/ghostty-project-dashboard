import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from dashboard import notes_keys, native

class NotesKeyConfigTests(unittest.TestCase):
    def test_install_is_additive_idempotent_and_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            home=Path(root); config=home/'Library/Application Support/com.mitchellh.ghostty/config'
            config.parent.mkdir(parents=True); original='font-size = 16\nkeybind = super+a=select_all\n';config.write_text(original)
            destination=home/'installed'
            notes_keys.install(home,destination); first=config.read_text()
            notes_keys.install(home,destination)
            self.assertEqual(first,config.read_text());self.assertTrue(first.startswith(original))
            self.assertEqual((destination/'ghostty-config-before-notes.conf').read_text(),original)
            self.assertTrue(all(line.startswith('keybind = super+') for line in notes_keys.CONFIG.splitlines()))
            self.assertNotIn('activate_key_table',notes_keys.CONFIG)
            self.assertNotIn('text:',notes_keys.CONFIG)

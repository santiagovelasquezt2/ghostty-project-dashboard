"""Installation payload checks without changing the user's installed app."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("dashboard_installer", Path(__file__).resolve().parents[1] / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_payload_excludes_local_environments_git_and_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "source", root / "installed"
            for name in ("dashboard/__init__.py", "tests/test_example.py", "docs/INSTALL.md",
                         "pyproject.toml", "README.md", "AGENTS.md", "Brewfile", "uv.lock", "install.py",
                         ".venv/large-file", ".git/config", ".env", "work/private.txt",
                         "personal-notes.txt", "dashboard/__pycache__/cache.pyc"):
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture\n")
            installer.copy_payload(source, destination)
            self.assertTrue((destination / "dashboard/__init__.py").is_file())
            self.assertTrue((destination / "docs/INSTALL.md").is_file())
            self.assertTrue((destination / "uv.lock").is_file())
            for name in (".venv", ".git", ".env", "work", "personal-notes.txt", "dashboard/__pycache__"):
                self.assertFalse((destination / name).exists(), name)

    def test_copying_from_installed_payload_is_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "README.md").write_text("keep\n")
            installer.copy_payload(path, path)
            self.assertEqual((path / "README.md").read_text(), "keep\n")

    def test_unsupported_platform_is_rejected_before_filesystem_changes(self):
        with patch.object(installer.sys, "platform", "linux"), patch.object(installer.Path, "home") as home:
            with self.assertRaisesRegex(SystemExit, "macOS"):
                installer.main()
            home.assert_not_called()

    def test_old_python_is_rejected_before_filesystem_changes(self):
        with patch.object(installer.sys, "platform", "darwin"), \
                patch.object(installer.sys, "version_info", (3, 9)), patch.object(installer.Path, "home") as home:
            with self.assertRaisesRegex(SystemExit, "Python 3.11"):
                installer.main()
            home.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from dashboard.gitdata import commit_diff, discover_root, display_path, file_diff, snapshot


class GitDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dashboard-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name, "project with spaces").resolve()
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Dashboard Test")
        self.git("config", "user.email", "dashboard@example.invalid")
        self.write("src/app/page.tsx", "first\nsecond\nthird\n")
        self.write("src/rename me.ts", "keep\nthese\nlines\n")
        self.write("public/old-logo.svg", "logo\nold\n")
        self.write("picture.bin", b"old\0binary\n")
        self.write(".gitignore", "node_modules/\n")
        self.commit("Initial project")
        self.git("checkout", "-b", "feature/account")

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", "-C", str(cwd or self.root), *args], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode().strip()

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())
        return target

    def commit(self, message="Change files"):
        self.git("add", "--all")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def read(self):
        state = snapshot(str(self.root))
        self.assertIsNone(state.error, state.error)
        return state

    @staticmethod
    def by_path(files):
        return {item.path: item for item in files}

    def test_modified_colour_survives_commit_and_clears_after_merge(self):
        self.write("src/app/page.tsx", "first\nupdated\nthird\n")
        before = self.read()
        self.assertEqual(before.committed_files, [])
        self.assertEqual(len(before.all_files), 1)
        self.assertEqual((before.all_files[0].status, before.all_files[0].uncommitted), ("M", True))
        self.commit("Update page")
        after = self.read()
        self.assertEqual(after.committed_files[0].status, "M")
        self.assertEqual((after.all_files[0].status, after.all_files[0].uncommitted), ("M", False))
        self.assertEqual(len(after.commits), 1)
        self.assertEqual(after.commits[0].subject, "Update page")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "feature/account", "-m", "Merge feature")
        self.git("checkout", "feature/account")
        merged = self.read()
        self.assertEqual(merged.committed_files, [])
        self.assertEqual(merged.all_files, [])
        self.assertEqual(merged.commits, [])

    def test_modes_count_combined_and_committed_net_lines(self):
        self.write("src/app/page.tsx", "first\nupdated\nthird\n")
        self.commit("Committed update")
        self.write("src/app/page.tsx", "first\nupdated\nthird\nnew line\n")
        self.write("new module.ts", "one\ntwo\n")
        state = self.read()
        self.assertEqual(sum(item.added for item in state.committed_files), 1)
        self.assertEqual(sum(item.deleted for item in state.committed_files), 1)
        self.assertEqual(sum(item.added for item in state.all_files), 4)
        self.assertEqual(sum(item.deleted for item in state.all_files), 1)
        self.assertTrue(all(item.uncommitted for item in state.all_files))
        self.assertIn("+new line", file_diff(str(self.root), state.base_sha, "src/app/page.tsx"))
        self.assertNotIn("+new line", file_diff(str(self.root), state.base_sha, "src/app/page.tsx", True, state.head))

    def test_staged_added_deleted_and_renamed_files(self):
        self.write("src/new.ts", "new\nmodule\n")
        self.git("rm", "public/old-logo.svg")
        self.git("mv", "src/rename me.ts", "src/renamed ü.ts")
        self.git("add", "src/new.ts")
        state = self.read()
        files = self.by_path(state.all_files)
        self.assertEqual(files["src/new.ts"].status, "A")
        self.assertEqual((files["public/old-logo.svg"].status, files["public/old-logo.svg"].deleted), ("D", 2))
        renamed = files["src/renamed ü.ts"]
        self.assertEqual((renamed.status, renamed.old_path, renamed.added, renamed.deleted), ("M", "src/rename me.ts", 0, 0))
        self.assertTrue(renamed.uncommitted)
        self.commit()
        files = self.by_path(self.read().committed_files)
        self.assertEqual(files["src/new.ts"].status, "A")
        self.assertEqual(files["public/old-logo.svg"].status, "D")
        self.assertIn("rename from", file_diff(str(self.root), state.base_sha, renamed.path, True, "HEAD", renamed.old_path))

    def test_main_advancing_does_not_show_unrelated_files(self):
        self.write("src/app/page.tsx", "first\nfeature\nthird\n")
        self.commit("Feature work")
        self.git("checkout", "main")
        self.write("unrelated.txt", "main only\n")
        self.commit("Main moves forward")
        self.git("checkout", "feature/account")
        state = self.read()
        self.assertEqual([item.path for item in state.committed_files], ["src/app/page.tsx"])
        self.assertEqual([item.subject for item in state.commits], ["Feature work"])

    def test_history_excludes_main_ancestors_in_crisscross_merges(self):
        ancestor = self.git("rev-parse", "HEAD")
        tree = self.git("rev-parse", "HEAD^{tree}")
        left = self.git("commit-tree", tree, "-p", ancestor, "-m", "Shared left")
        right = self.git("commit-tree", tree, "-p", ancestor, "-m", "Shared right")
        feature = self.git("commit-tree", tree, "-p", left, "-p", right, "-m", "Feature merge")
        main = self.git("commit-tree", tree, "-p", right, "-p", left, "-m", "Main merge")
        self.git("update-ref", "refs/heads/feature/account", feature)
        self.git("update-ref", "refs/heads/main", main)
        state = self.read()
        self.assertEqual([item.subject for item in state.commits], ["Feature merge"])

    def test_origin_main_selected_when_provably_newer(self):
        old_main = self.git("rev-parse", "main")
        self.write("feature.txt", "ready\n")
        feature = self.commit("Feature")
        self.git("update-ref", "refs/remotes/origin/main", feature)
        self.assertEqual(self.git("rev-parse", "main"), old_main)
        state = self.read()
        self.assertEqual(state.base, "origin/main")
        self.assertEqual(state.committed_files, [])

    def test_diverged_origin_main_does_not_override_local_main(self):
        baseline = self.git("rev-parse", "main")
        self.write("feature.txt", "feature\n")
        self.commit("Feature")
        self.git("checkout", "main")
        self.write("local.txt", "local\n")
        self.commit("Local main")
        self.git("checkout", "-b", "remote-other", baseline)
        self.write("remote.txt", "remote\n")
        remote = self.commit("Remote diverges")
        self.git("update-ref", "refs/remotes/origin/main", remote)
        self.git("checkout", "feature/account")
        state = self.read()
        self.assertEqual(state.base, "main")
        self.assertEqual([item.path for item in state.all_files], ["feature.txt"])

    def test_binary_and_ignored_untracked_files(self):
        self.write("picture.bin", b"new\0binary content\n")
        self.write("new.bin", b"new\0blob\n")
        self.write("node_modules/ignored.js", "ignored\n" * 100)
        state = self.read()
        self.assertEqual({item.path for item in state.all_files}, {"picture.bin", "new.bin"})
        self.assertTrue(all(item.binary for item in state.all_files))
        self.assertTrue(all(item.added == item.deleted == 0 for item in state.all_files))
        self.assertIn("Binary file", file_diff(str(self.root), state.base_sha, "new.bin"))

    def test_nested_file_discovery_and_linked_worktree(self):
        self.assertEqual(discover_root(str(self.root / "src/app/page.tsx")), str(self.root))
        self.assertEqual(discover_root(str(self.root / "src/app")), str(self.root))
        worktree = Path(self.temp.name, "linked worktree").resolve()
        self.git("worktree", "add", "-b", "linked", str(worktree), "main")
        (worktree / "src/app/page.tsx").write_text("changed\n")
        state = snapshot(str(worktree / "src/app"))
        self.assertIsNone(state.error)
        self.assertEqual(state.root, str(worktree))
        self.assertEqual(state.branch, "linked")
        self.assertEqual(state.all_files[0].status, "M")

    def test_empty_repository_and_fallback_without_main(self):
        empty = Path(self.temp.name, "empty")
        empty.mkdir()
        self.git("init", "-b", "main", cwd=empty)
        (empty / "first.txt").write_text("first\n")
        state = snapshot(str(empty))
        self.assertIsNone(state.error, state.error)
        self.assertEqual(state.branch, "main")
        self.assertEqual(state.head, "")
        self.assertEqual(state.commits, [])
        self.assertEqual((state.all_files[0].status, state.all_files[0].added), ("A", 1))
        self.git("branch", "-D", "main")
        self.write("after-root.txt", "one\n")
        self.commit("Second commit")
        state = self.read()
        self.assertEqual(state.base, "root commit")
        self.assertEqual([item.path for item in state.all_files], ["after-root.txt"])

    def test_file_names_are_literal_and_terminal_safe(self):
        name = "src/[literal]* ü\n\x1b[31m.ts"
        self.write(name, "safe\n\x1b[2Jinert\n")
        self.write("src/other.ts", "unrelated\n")
        state = self.read()
        self.assertIn(name, self.by_path(state.all_files))
        preview = file_diff(str(self.root), state.base_sha, name)
        self.assertIn("inert", preview)
        self.assertNotIn("unrelated", preview)
        self.assertNotIn("\x1b", preview)
        self.assertNotIn("\n", display_path(name))
        self.assertNotIn("\x1b", display_path(name))
        self.commit("Subject \x1b[2J safe")
        state = self.read()
        self.assertNotIn("\x1b", state.commits[0].subject)
        self.assertNotIn("\x1b", commit_diff(str(self.root), state.head))
        self.assertIn("inert", file_diff(str(self.root), state.base_sha, name, True, state.head))

    def test_untracked_symlink_does_not_read_target(self):
        secret = Path(self.temp.name, "outside.txt")
        secret.write_text("not the symlink contents\n")
        (self.root / "link.txt").symlink_to(secret)
        state = self.read()
        self.assertEqual(state.all_files[0].added, 1)
        preview = file_diff(str(self.root), state.base_sha, "link.txt")
        self.assertNotIn("not the symlink contents", preview)
        self.assertIn("outside.txt", preview)

    def test_index_deletion_with_untracked_recreation_uses_net_contents(self):
        self.git("rm", "--cached", "src/app/page.tsx")
        state = self.read()
        self.assertEqual(state.all_files, [])
        self.write("src/app/page.tsx", "first\nnew second\nthird\n")
        state = self.read()
        self.assertEqual(len(state.all_files), 1)
        item = state.all_files[0]
        self.assertEqual((item.status, item.added, item.deleted), ("M", 1, 1))

    def test_binary_recreation_and_binary_attributes(self):
        self.git("rm", "--cached", "picture.bin")
        self.assertEqual(self.read().all_files, [])
        self.write(".gitattributes", "*.dat -diff\n")
        self.write("opaque.dat", "looks like text\nbut counted as binary\n")
        state = self.read()
        item = self.by_path(state.all_files)["opaque.dat"]
        self.assertTrue(item.binary)
        self.assertEqual(item.added, 0)
        self.assertIn("Binary file", file_diff(str(self.root), state.base_sha, item.path))

    def test_invalid_base_and_nonrepo_fail_usefully(self):
        state = snapshot(str(self.root), "missing-branch")
        self.assertIn("does not exist", state.error)
        with self.assertRaisesRegex(ValueError, "not an accessible Git project"):
            discover_root(self.temp.name)

    def test_net_revert_disappears_from_tree(self):
        self.write("src/app/page.tsx", "one\ntwo\n")
        self.commit("Temporary change")
        self.write("src/app/page.tsx", "first\nsecond\nthird\n")
        state = self.read()
        self.assertEqual(state.all_files, [])
        self.assertEqual(len(state.committed_files), 1)

    def test_previews_disable_external_diff_and_text_conversion(self):
        self.write(".gitattributes", "*.tsx diff=dashboard-test\n")
        self.commit("Set attributes")
        self.git("config", "diff.external", "false")
        self.git("config", "diff.dashboard-test.textconv", "false")
        self.write("src/app/page.tsx", "first\nupdated\nthird\n")
        state = self.read()
        self.assertIn("+updated", file_diff(str(self.root), state.base_sha, "src/app/page.tsx"))
        head = self.commit("Update with custom diff configured")
        self.assertIn("+updated", commit_diff(str(self.root), head))

    def test_large_previews_are_bounded(self):
        self.write("large.txt", "new line of text\n" * 20_000)
        state = self.read()
        preview = file_diff(str(self.root), state.base_sha, "large.txt")
        self.assertIn("Preview truncated", preview)
        self.assertLess(len(preview), 180_000)
        self.commit("Large text")
        preview = commit_diff(str(self.root), self.git("rev-parse", "HEAD"))
        self.assertIn("Preview truncated", preview)
        self.assertLess(len(preview), 170_000)


if __name__ == "__main__":
    unittest.main()

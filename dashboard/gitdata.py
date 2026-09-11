"""Read-only Git data for the dashboard.

File statuses describe the net difference from the branch's merge base. They do
not describe just the index, so committing does not clear a file's colour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time


MAX_FILES = 5000
MAX_UNTRACKED_FILES = 1000
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_UNTRACKED_BYTES = 64 * 1024 * 1024
MAX_PREVIEW_BYTES = 160_000
MAX_COMMITS = 2000
COMMAND_TIMEOUT = 8
_BIDI = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


@dataclass
class Commit:
    sha: str
    subject: str
    date: str = ""


@dataclass
class FileChange:
    path: str
    status: str
    added: int = 0
    deleted: int = 0
    old_path: str | None = None
    binary: bool = False
    uncommitted: bool = False


@dataclass
class Snapshot:
    root: str
    branch: str
    base: str
    base_sha: str
    head: str
    commits: list[Commit] = field(default_factory=list)
    committed_files: list[FileChange] = field(default_factory=list)
    all_files: list[FileChange] = field(default_factory=list)
    error: str | None = None


class GitError(ValueError):
    pass


def safe_text(value: str) -> str:
    """Keep multiline text readable without executable terminal controls."""
    return "".join(
        f"\\u{ord(c):04x}" if c in _BIDI or 0xD800 <= ord(c) <= 0xDFFF
        else f"\\x{ord(c):02x}" if (ord(c) < 32 and c not in "\n\t") or 127 <= ord(c) < 160
        else c for c in value
    )


def display_path(value: str) -> str:
    """Escape newlines and controls in a filename; retain the raw path for Git."""
    return safe_text(value).replace("\n", "\\n").replace("\t", "\\t")


def _decode(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _git(root: str, *args: str, allow: tuple[int, ...] = (0,),
         limit: int = MAX_METADATA_BYTES, truncate: bool = False,
         input_data: bytes | None = None) -> bytes:
    env = os.environ.copy()
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
    command = ["git", "--no-pager", "--literal-pathspecs", "-c", "color.ui=false",
               "-c", "core.fsmonitor=false", "-c", "core.quotepath=false", "-C", root, *args]
    # Spool output instead of buffering a potentially enormous diff in memory.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(command, input=input_data, stdout=output, stderr=errors,
                                    env=env, timeout=COMMAND_TIMEOUT, check=False)
        except subprocess.TimeoutExpired as exc:
            raise GitError("Git took too long; try again after the repository is less busy.") from exc
        except OSError as exc:
            raise GitError(f"Unable to run Git: {exc.strerror or str(exc)}") from exc
        if result.returncode not in allow:
            errors.seek(0)
            detail = safe_text(_decode(errors.read(1500))).strip()
            raise GitError(detail or f"Git exited with status {result.returncode}.")
        output.seek(0)
        data = output.read(limit + 1)
        if len(data) > limit:
            if not truncate:
                raise GitError("Repository results exceed the dashboard's size limit; narrow the project or commit untracked files.")
            return data[:limit] + b"\n\n[Preview truncated; open the file for the complete contents.]\n"
        return data


def discover_root(path: str) -> str:
    candidate = Path(path).expanduser().absolute()
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.is_dir():
        raise ValueError(f"Project path does not exist: {display_path(str(candidate))}")
    try:
        return _decode(_git(str(candidate), "rev-parse", "--show-toplevel")).rstrip("\n")
    except GitError as exc:
        raise ValueError(f"This folder is not an accessible Git project: {display_path(str(candidate))}") from exc


def _revision(root: str, ref: str) -> str | None:
    try:
        return _decode(_git(root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")).strip()
    except GitError:
        return None


def _ancestor(root: str, older: str, newer: str) -> bool:
    # merge-base --is-ancestor returns 1 for a valid negative answer.
    marker = _git(root, "rev-list", "--max-count=1", f"{older}", "--not", f"{newer}", "--")
    return not marker.strip()


def _empty_tree(root: str) -> str:
    return _decode(_git(root, "hash-object", "-t", "tree", "--stdin", input_data=b"")).strip()


def _base(root: str, head: str, override: str | None) -> tuple[str, str, str | None]:
    if override:
        tip = _revision(root, override)
        if not tip:
            raise GitError(f"Comparison branch does not exist: {display_path(override)}")
        name = override
    else:
        tip, name = None, ""
        for branch in ("main", "master"):
            local = _revision(root, f"refs/heads/{branch}")
            remote = _revision(root, f"refs/remotes/origin/{branch}")
            if local or remote:
                tip, name = local or remote, branch if local else f"origin/{branch}"
                if local and remote and local != remote and _ancestor(root, local, remote):
                    tip, name = remote, f"origin/{branch}"
                break
        if not tip:
            remote_default = _decode(_git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", allow=(0, 1))).strip()
            if remote_default:
                tip = _revision(root, remote_default)
                name = remote_default.removeprefix("refs/remotes/")
        if not tip:
            if not head:
                return "initial commit", _empty_tree(root), None
            roots = _decode(_git(root, "rev-list", "--max-parents=0", head, "--")).splitlines()
            return "root commit", roots[-1], roots[-1]
    if not head:
        return name, _empty_tree(root), tip
    common = _decode(_git(root, "merge-base", head, tip, allow=(0, 1))).strip()
    return name, common.splitlines()[0] if common else _empty_tree(root), tip


def _numstat(raw: bytes) -> dict[str, tuple[int, int, bool]]:
    tokens = raw.split(b"\0")
    result: dict[str, tuple[int, int, bool]] = {}
    index = 0
    while index < len(tokens) and tokens[index]:
        added, removed, path = tokens[index].split(b"\t", 2)
        index += 1
        if not path:  # With -z, rename statistics contain old and new path tokens.
            index += 1
            path = tokens[index]
            index += 1
        binary = added == b"-" or removed == b"-"
        result[_decode(path)] = (0 if binary else int(added), 0 if binary else int(removed), binary)
    return result


def _changes(root: str, base: str, head: str | None) -> list[FileChange]:
    revisions = [base, head] if head else [base]
    options = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames", "--ignore-submodules=none"]
    statuses = _git(root, *options, "--name-status", "-z", *revisions, "--").split(b"\0")
    stats = _numstat(_git(root, *options, "--numstat", "-z", *revisions, "--"))
    result: list[FileChange] = []
    index = 0
    while index < len(statuses) and statuses[index]:
        code = chr(statuses[index][0])
        path = _decode(statuses[index + 1])
        old_path = None
        index += 2
        if code in ("R", "C"):
            old_path, path = path, _decode(statuses[index])
            index += 1
        added, removed, binary = stats.get(path, (0, 0, False))
        result.append(FileChange(path, code if code in ("A", "D") else "M", added, removed, old_path, binary))
        if len(result) > MAX_FILES:
            raise GitError(f"More than {MAX_FILES:,} changed files; this exceeds the dashboard display limit.")
    return result


def _working_status(root: str) -> tuple[set[str], list[str]]:
    tokens = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                  "--ignore-submodules=none").split(b"\0")
    dirty: set[str] = set()
    untracked: list[str] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        code = tokens[index][:2]
        path = _decode(tokens[index][3:])
        dirty.add(path)
        index += 1
        if code == b"??":
            untracked.append(path)
            if len(untracked) > MAX_UNTRACKED_FILES:
                raise GitError(f"More than {MAX_UNTRACKED_FILES:,} untracked files. Add generated folders to .gitignore to keep the dashboard responsive.")
        elif b"R" in code or b"C" in code:
            dirty.add(_decode(tokens[index]))
            index += 1
    return dirty, untracked


def _untracked_data(root: str, path: str, budget: list[int], preview: bool = False) -> tuple[bytes, bool]:
    filename = os.path.join(root, path)
    try:
        info = os.lstat(filename)
        if stat.S_ISLNK(info.st_mode):
            return os.fsencode(os.readlink(filename)), False
        if not stat.S_ISREG(info.st_mode):
            return b"", True
        limit = MAX_PREVIEW_BYTES if preview else min(budget[0], MAX_UNTRACKED_BYTES)
        with os.fdopen(os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as source:
            first = source.read(min(limit + 1, 8000))
            if b"\0" in first:
                return b"", True
            if not preview and info.st_size > limit:
                raise GitError("Untracked text exceeds the 64 MiB scan limit. Commit it or ignore generated files before calculating totals.")
            data = first + source.read(max(0, limit + 1 - len(first)))
            if len(data) > limit and not preview:
                raise GitError("Untracked files exceed the dashboard's scan limit.")
            budget[0] -= len(data)
            return data, False
    except FileNotFoundError:
        raise GitError("A file changed while the dashboard refreshed; refreshing again will update the view.") from None
    except OSError as exc:
        raise GitError(f"Cannot read {display_path(path)}: {exc.strerror or str(exc)}") from exc


def _untracked_change(root: str, base: str, path: str, previous: FileChange | None,
                      budget: list[int], binary_attribute: bool = False) -> FileChange | None:
    if previous and previous.status == "D":
        # A removed index entry can still exist as an untracked working file.
        # Compare its contents so combined totals do not count a false deletion.
        original = _git(root, "show", f"{base}:{path}", limit=MAX_UNTRACKED_BYTES)
        with tempfile.TemporaryDirectory(prefix="dashboard-git-") as temporary:
            old = Path(temporary, "before")
            old.write_bytes(original)
            new = Path(root, path)
            if new.is_symlink():
                target = os.fsencode(os.readlink(new))
                new = Path(temporary, "after")
                new.write_bytes(target)
            raw = _git(root, "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--numstat", "-z",
                       "--", str(old), str(new), allow=(0, 1))
            values = list(_numstat(raw).values())
            if not values:
                return None
            added, deleted, is_binary = values[0]
            if binary_attribute:
                added, deleted, is_binary = 0, 0, True
            return FileChange(path, "M", added, deleted, binary=is_binary, uncommitted=True)
    if binary_attribute:
        return FileChange(path, "A", binary=True, uncommitted=True)
    data, binary = _untracked_data(root, path, budget)
    lines = data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))
    return FileChange(path, "A", lines, 0, binary=binary, uncommitted=True)


def _binary_attributes(root: str, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    entries = _git(root, "check-attr", "-z", "--stdin", "diff",
                   input_data=b"\0".join(os.fsencode(path) for path in paths) + b"\0").split(b"\0")
    return {_decode(entries[index]) for index in range(0, len(entries) - 2, 3)
            if entries[index + 2] == b"unset"}


def snapshot(root: str, base_override: str | None = None) -> Snapshot:
    result = Snapshot(root=root, branch="", base=base_override or "main", base_sha="", head="")
    try:
        result.root = discover_root(root)
        result.head = _revision(result.root, "HEAD") or ""
        result.branch = _decode(_git(result.root, "symbolic-ref", "--quiet", "--short", "HEAD", allow=(0, 1))).strip()
        result.branch = safe_text(result.branch or f"detached {result.head[:8]}")
        result.base, result.base_sha, base_tip = _base(result.root, result.head, base_override)
        if result.head:
            # History excludes everything already reachable from the selected main
            # tip, including additional ancestors in unusual multi-merge histories.
            # File diffs still use the merge base so unrelated main changes stay out.
            base_commit = base_tip
            revs = [f"{base_commit}..{result.head}"] if base_commit else [result.head]
            raw_commits = _git(result.root, "log", f"--max-count={MAX_COMMITS + 1}", "--format=%H%x00%s%x00%cI%x00", *revs, "--")
            records = raw_commits.split(b"\0")
            for index in range(0, len(records) - 2, 3):
                result.commits.append(Commit(_decode(records[index]).strip(), safe_text(_decode(records[index + 1])), _decode(records[index + 2])))
            if len(result.commits) > MAX_COMMITS:
                raise GitError(f"More than {MAX_COMMITS:,} branch commits; the dashboard history limit has been reached.")
            result.committed_files = _changes(result.root, result.base_sha, result.head)
        result.all_files = _changes(result.root, result.base_sha, None)
        dirty, untracked = _working_status(result.root)
        by_path = {item.path: item for item in result.all_files}
        for item in result.all_files:
            item.uncommitted = item.path in dirty or item.old_path in dirty
        budget = [MAX_UNTRACKED_BYTES]
        deadline = time.monotonic() + COMMAND_TIMEOUT
        binary_attributes = _binary_attributes(result.root, untracked)
        for path in untracked:
            if time.monotonic() > deadline:
                raise GitError("Untracked file scanning took too long. Ignore generated files to keep the dashboard responsive.")
            item = _untracked_change(result.root, result.base_sha, path, by_path.get(path), budget,
                                     path in binary_attributes)
            if item is None:
                by_path.pop(path, None)
            else:
                by_path[path] = item
        result.all_files = sorted(by_path.values(), key=lambda item: item.path)
        if len(result.all_files) > MAX_FILES:
            raise GitError(f"More than {MAX_FILES:,} changed files; this exceeds the dashboard display limit.")
    except (GitError, ValueError, OSError) as exc:
        result.error = safe_text(str(exc))
    return result


def _preview_untracked(root: str, base: str, path: str) -> str:
    if path in _binary_attributes(root, [path]):
        return f"Binary file: {display_path(path)}\nLine totals do not include binary files."
    data, binary = _untracked_data(root, path, [MAX_PREVIEW_BYTES], preview=True)
    if binary:
        return f"Binary file: {display_path(path)}\nLine totals do not include binary files."
    try:
        original = _git(root, "show", f"{base}:{path}", limit=MAX_PREVIEW_BYTES, truncate=True)
    except GitError:
        original = b""
    text = "".join(difflib.unified_diff(_decode(original).splitlines(keepends=True),
                                     _decode(data[:MAX_PREVIEW_BYTES]).splitlines(keepends=True),
                                     fromfile=f"a/{display_path(path)}" if original else "/dev/null",
                                     tofile=f"b/{display_path(path)}"))
    if len(data) > MAX_PREVIEW_BYTES:
        text += "\n[Preview truncated; open the file for the complete contents.]\n"
    return safe_text(text or "No content changes.")


def file_diff(root: str, base_sha: str, path: str, committed: bool = False,
              head: str = "HEAD", old_path: str | None = None) -> str:
    try:
        root = discover_root(root)
        if not committed and os.path.lexists(os.path.join(root, path)):
            tracked = _git(root, "ls-files", "--", path)
            if not tracked:
                return _preview_untracked(root, base_sha, path)
        revisions = [base_sha, head] if committed else [base_sha]
        paths = [old_path, path] if old_path and old_path != path else [path]
        raw = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames",
                   *revisions, "--", *paths, limit=MAX_PREVIEW_BYTES, truncate=True)
        return safe_text(_decode(raw)) or "No content changes."
    except (GitError, ValueError) as exc:
        return safe_text(str(exc))


def commit_diff(root: str, sha: str) -> str:
    """Return a bounded, inert patch for a selected commit."""
    try:
        revision = _revision(root, sha)
        if not revision:
            return "This commit is no longer available."
        return safe_text(_decode(_git(root, "show", "--format=fuller", "--stat", "--patch", "--no-ext-diff",
                                      "--no-textconv", "--no-color", revision, "--",
                                      limit=MAX_PREVIEW_BYTES, truncate=True)))
    except GitError as exc:
        return safe_text(str(exc))

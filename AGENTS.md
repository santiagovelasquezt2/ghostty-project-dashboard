# Working on Ghostty Project Dashboard

Read `README.md` first. For installation work, follow `docs/AGENT_SETUP.md`; for code changes, also read `docs/ARCHITECTURE.md`.

## Scope and environment

- This is a macOS application implemented in Python and launched as `dashboard` from a regular Ghostty terminal. Native layouts target Ghostty 1.3+ at `/Applications/Ghostty.app`.
- It is a complete, standalone source package. No originating conversation, personal dotfiles, Codex plugin, or external agent skill is needed.
- Git/file panels are custom Textual interfaces. LazyGit, Yazi, and Herdr are not runtime dependencies.
- Runtime dependencies: Python 3.11+, Textual, Git, tmux, btop. `uv` installs/manages Python and dependencies. `Brewfile` lists the macOS tools; `uv.lock` locks the development environment, including the `dev` extra.

## Development

```sh
uv sync --locked --extra dev --python 3.13
uv run --locked --extra dev python -m unittest discover -s tests -v
uv build
```

The `pyte` development extra is required for terminal mouse tests. Use `unittest`; there is no separate pytest test suite. Tests use disposable Git repositories and uniquely named tmux sockets.

After changing dependencies, update `uv.lock` with `uv lock`, review it, and rerun the affected checks. The user-facing installer copies an independent application into `~/.local/share/ghostty-project-dashboard`; editing this clone does not update that installed copy. Reinstall only when the task calls for it.

## Preserve these behaviors

- Normal launch reuses the invoking Ghostty window; Leave returns to its original shell. A tab that already contains unrelated splits gets a separate dashboard tab in the same window.
- Identify native surfaces by saved IDs and the invoking terminal's temporary title marker. Never guess the target using focus, a matching project directory, or a window title other than the exact marker.
- Keep origin ownership and generation metadata through toggle/update operations. An old launcher must not clean up a newer dashboard.
- Hide/Show and Leave preserve the coding process. Grok and OpenCode are ordinary programs inside that persistent terminal.
- Keep the background black, ancestor folders neutral, modified files blue, additions green, deletions red, and timestamps subdued with AM/PM.
- All/Committed totals follow the selected comparison. A new commit must not reset branch colors.
- Project observation is read-only: no automatic fetch, stage, commit, reset, or project-file changes.
- GPU measurements use the driver's counters. Preserve unknown/sampling states and context-change protections; never substitute CPU usage for GPU usage.

## Safe operations and evidence

- Never run `tmux kill-server` without an explicit test-only socket during tests. Production sockets are `ghostty-dashboard` and `ghostty-dashboard-resources`; stopping them can terminate active coding work.
- Do not overwrite global `.tmux.conf`, Ghostty preferences, shell profiles, coding-tool settings, or another `dashboard` command. The installer only replaces its own marked launcher.
- Keep environments, credentials, personal paths, live session JSON, logs, and screenshots of unrelated work out of commits.
- Native AppleScript execution affects a real desktop. Test script construction with mocks and syntax compilation first. Respect the environment's UI automation permissions; do not bypass a blocked application through another tool.
- Distinguish passing automated tests from native behavior actually observed. If desktop access is unavailable, report that limit and leave the manual checklist for the user.
- Do not submit a coding-assistant prompt just to test terminal rendering; an idle interface is sufficient.

## File map

| Area | Files |
| --- | --- |
| Launcher, bindings, tmux layout | `dashboard/cli.py` |
| Native Ghostty scripts and ownership checks | `dashboard/native.py` |
| Persistent windows, origin attach, Leave, generations | `dashboard/native_workspace.py` |
| Git baseline, status, commits, diffs | `dashboard/gitdata.py` |
| Commit list and changed-file tree | `dashboard/panels.py` |
| CPU/RAM and process sampling | `dashboard/processdata.py` |
| GPU counters and delta accounting | `dashboard/gpudata.py` |
| Process table and filters | `dashboard/processes.py` |
| Right column and btop/compact fallback | `dashboard/resource_stack.py`, `dashboard/resource_graphs.py` |
| Installation | `install.py` |

# Agent setup runbook

Use this alongside `AGENTS.md`. The repo is self-contained; do not look for the original author's home directory, private conversations, dotfiles, or installed skills.

## 1. Inspect the target Mac

Check only the necessary information:

```sh
uname -s
uname -m
command -v brew
command -v git
command -v tmux
command -v btop
command -v uv
```

Confirm Ghostty is at `/Applications/Ghostty.app` and supports the included scripting API. Its version can be checked without opening a window:

```sh
/Applications/Ghostty.app/Contents/MacOS/ghostty +version
```

Do not dump the user's environment or credentials. Check whether `~/.local/bin/dashboard` is already present and whether it is this project's marked launcher. Do not replace an unrelated command or stop an existing session.

## 2. Install

Follow [INSTALL.md](INSTALL.md). Install missing Homebrew dependencies and run:

```sh
uv run --no-project --python 3.13 python install.py
export PATH="$HOME/.local/bin:$PATH"
dashboard --version
```

If persistent PATH setup is needed, preserve the existing shell profile and add the single PATH line only once. Do not replace the whole file. Coding assistants are optional and separate: ask the user which one only if installing one is actually part of the task. This dashboard does not need an API key.

## 3. Run automated checks

From the repository root:

```sh
uv sync --locked --extra dev --python 3.13
uv run --locked --extra dev python -m unittest discover -s tests -v
uv build
```

The suite creates temporary repositories and separate tmux sockets. It does not require a running Ghostty desktop. Native actions are mocked; GPU driver parsing is tested with fixtures. The `pyte` extra ensures mouse-click tests are not silently skipped.

For an additional install smoke test, use a disposable destination by importing `install.py` in a test harness and patching `Path.home`; do not repurpose the shell's `$HOME` variable. Do not install over or stop the user's working dashboard just to prove an installation can succeed.

## 4. Manual acceptance checks

Perform these in a disposable Git project with a feature branch and a few added, modified, and deleted files. If UI access to Ghostty is blocked, do not bypass the restriction; give the user this checklist and report the native transition as unverified.

1. From a normal Ghostty shell, run `dashboard`. Verify the current window becomes the dashboard and no additional window appears.
2. Check commits/count, subdued AM/PM timestamps, neutral parent folders, and blue/green/red changed filenames. Toggle All/Committed and check the corresponding `+`/`−` totals.
3. Make a commit in the disposable project. Confirm branch changes remain colored relative to the baseline.
4. In the center, start a shell or an already installed coding tool. Verify typing, scrolling, and paste. Do not submit a paid/remote AI prompt just to test rendering.
5. With the center focused, test **⌘+**, **⌘−**, and **⌘0**. Only its text should change size. Resize borders and check redraw.
6. Hide and show the coding terminal. Verify the same process is still running. Reopening the native surface currently resets its font size and equalizes column widths.
7. Click **Leave**. Verify a clean original shell in the same directory and window. Run `dashboard` again and confirm the coding process survived. Repeat with **Ctrl-b**, then **d**.
8. Click the CPU, GPU, and Memory tabs. Check names, filtering, and refresh. On supported Apple Silicon, allow two samples before judging GPU percentages. Unsupported counters must show unavailable rather than invented values.
9. If the user normally has multiple tabs or unrelated splits, test from that layout too. Existing splits get a dashboard tab in the same window; Leave must return to the original tab without closing its other terminals.

Do not assume a passing Python suite proves these desktop behaviors. Report exact checks completed, any skips, and the installed version.

## 5. Handoff

Tell the user that the launch command is `dashboard`, how Leave works, where the installed copy lives, and any actual verification limits. Link the repository and relevant troubleshooting page. Preserve active coding work and do not leave disposable test servers running.

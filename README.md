# Ghostty project dashboard

One command for a black-background Git workspace, a persistent coding terminal, and live CPU/GPU/memory process views on macOS.

```text
┌───────────────────┬────────────────────┬───────────────────────┐
│ Branch commits    │                    │ CPU + memory graphs   │
│ Count + date/time │ Coding terminal    │                       │
├───────────────────┤                    ├───────────────────────┤
│ Changed-file map  │ Shell / Grok /     │ Processes             │
│ All / Committed   │ OpenCode / ...     │ CPU / GPU / Memory    │
│ Colored + / −     │                    │ Filter + live values  │
└───────────────────┴────────────────────┴───────────────────────┘
```

## Install

Requires **macOS**, **Ghostty 1.3+** in `/Applications/Ghostty.app`, and [Homebrew](https://brew.sh). Per-process GPU measurements additionally require a compatible Apple Silicon driver. Linux and Windows are not supported by this installer.

```sh
brew install --cask ghostty
brew install git tmux btop uv
git clone https://github.com/santiagovelasquezt2/ghostty-project-dashboard.git
cd ghostty-project-dashboard
uv run --no-project --python 3.13 python install.py
export PATH="$HOME/.local/bin:$PATH"
dashboard --version
```

The explicit Python selection avoids the older Python bundled with some macOS installations. If Python 3.11+ is already your `python3`, `python3 install.py` also works. Add the PATH line to your shell startup file once if you want it available in future terminals. [Full installation, update, and removal instructions](docs/INSTALL.md).

Open **Ghostty**, enter a Git project, and run `dashboard`. Allow Ghostty automation if macOS prompts. There is no API key or account sign-in required for the dashboard itself. Install and authenticate any coding assistant separately.

**For another agent:** read [AGENTS.md](AGENTS.md) and follow [the setup runbook](docs/AGENT_SETUP.md). You can give it this prompt:

> Set up https://github.com/santiagovelasquezt2/ghostty-project-dashboard on this Mac using AGENTS.md and docs/AGENT_SETUP.md. Preserve my existing terminal configuration and coding sessions. Run the checks and distinguish automated test results from native Ghostty behavior you actually verify.

## Use

From any folder inside a Git project, run:

```sh
dashboard
```

The command turns the Ghostty terminal you launch it from into a dashboard with three native columns. It uses that same window. The left contains commit history above the changed-file tree, the center is the **coding terminal** for your normal shell, Grok, OpenCode, or another tool, and the right shows running processes. Each column has its own terminal surface, so clicking the coding terminal and pressing **⌘ + / −** changes only its text size. Borders can be dragged to resize. The two left sections have fixed positions; their pane-moving shortcuts and menus are disabled.

**Leave** removes the dashboard columns and returns to the original shell and directory, with a clear terminal screen. Your coding session stays running in the background. If the starting tab already contains other splits, the dashboard uses a new tab in that same window and Leave returns to the untouched original tab.

The native launcher was developed against Ghostty 1.3.1. Running `dashboard` from a normal Ghostty terminal also moves an existing older dashboard into that window without restarting the coding session. Run `dashboard --tmux` to use the single-surface layout with shared text size.

The launcher briefly marks its own terminal title to identify the correct window even if focus changes. Ghostty configurations with a fixed `title` prevent this lookup; the command reports an error instead of targeting a different window. The marker is replaced with the directory name afterward, and shell title updates continue normally.

The dashboard uses a black base background throughout, with charcoal selection states and neutral gray folder labels. Blue, green, and red remain reserved for change status, line totals, and useful accents. Programs you run in the center can still draw their own application theme.

Each commit includes a compact, faded date and time on the right, in your Mac's local timezone using **12-hour time with AM/PM**. The timestamp stays on the same line as the message; commits from earlier years also show the year.

## Controls

| Action | Control |
| --- | --- |
| Resize the sections | Drag a dividing border |
| Enlarge / shrink Grok text only | Click the center, then **⌘ + / −** |
| Reset Grok text size | Click the center, then **⌘ 0** |
| Hide / restore the coding terminal | Click **Hide coding terminal / Show coding terminal**, or **F2**, or **Ctrl-b**, then **t** |
| Return to a normal terminal, keeping work running | Click **Leave**, or **Ctrl-b**, then **d** |
| Return to this project's dashboard | Run `dashboard` again |
| Move focus between sections | Click a section |
| Resize the two left sections with the keyboard | **Ctrl-b**, then **Ctrl + Up / Down** |
| Show dashboard help | Click **Help** in the bottom bar |
| Switch file-tree mode | Click **All changes** / **Committed**, or press **a** / **c** in that section |
| Expand / collapse folders | Click the arrow, or use **Left / Right** |
| Scroll a large file map | Scroll up/down; use **Shift + scroll** or **Shift + Left / Right** sideways, or drag the scrollbars |
| View a file's diff or a commit | Select it and click **Open**, or press **Enter** |
| Return from a diff | Click **Back**, or press **Escape** |
| Refresh the selected Git panel | Click **Refresh**, or press **r** |
| Switch process view | Click **CPU / GPU / Memory**, or press **c / g / m** there |
| Filter process names | Click **Filter** or press **/**; **Clear** or **Escape** clears it |
| Refresh / select a process | Click **Refresh**, **↑**, or **↓** at the bottom of the process view |

On a Mac keyboard, F2 may require the Fn key. The Ctrl-b, t shortcut works without changing macOS keyboard settings. The bottom button updates between Hide and Show, and always controls the same coding terminal regardless of the program running inside it. On narrow columns, its label shortens to **Hide coding / Show coding**. **Leave** remains available from 24 columns wide; **Help** appears from 42 columns. Blank bottom-bar space does nothing when clicked.

Hiding the center preserves its running shell and CLI process. Ghostty's current scripting interface cannot read the zoom level or column widths, so restoring the center uses the configured font size and equalizes the columns. Resize or zoom again as needed. If you exit the center shell, press F2 to start it again. Closing or leaving Ghostty does not stop the dashboard's running processes; run `dashboard` in that project to return.

Ghostty's own native split controls remain available; its scripting interface cannot lock native column positions. The left commit and file sections remain fixed inside their column. The `--tmux` layout keeps all four positions fixed and restores their saved dimensions, but its text zoom affects the entire surface.

## File map and line totals

The tree includes changed files and their complete folder ancestry. Context folders stay neutral. Modified filenames are blue, added filenames green, and deleted filenames red. A renamed file is blue with its previous location shown beside it. `*` marks unfinished work.

**All changes** shows the net branch changes plus the current working files and new, untracked files. **Committed** shows only the changes saved in the branch's commits. The bright green `+` and red `−` totals always follow the selected mode. They count the current overall difference, rather than adding every intermediate edit to the same line. Binary files remain visible but contribute no line count. Ignored files are excluded.

The comparison uses the common starting point with `main`, so unrelated changes newly added to main do not appear in your branch's tree. Making a commit does not reset the branch colors. A fully reverted change disappears because it no longer differs from the baseline.

The dashboard reads local Git information every two seconds. It does not fetch, stage, commit, or modify project files. A merge is recognized once your locally known `main` or `origin/main` includes the branch. A remote merge will not be visible until those references are updated normally. Squash/rebase merges replace commit identities, so Git ancestry alone cannot reliably recognize every such merge; starting a new branch from updated main provides a clean baseline.

The comparison automatically chooses `main` (or a provably newer `origin/main`), then `master`, the remote's default branch, or the repository's root commit. The header shows the selected baseline. To choose another branch explicitly:

```sh
dashboard --base develop
```

Use that option when first opening the project's dashboard. An existing workspace keeps its comparison choice. A path argument can point to a project, a nested folder, or a file inside it:

```sh
dashboard /path/to/project/src/app/page.tsx
```

## Grok and screen size

The center is a real terminal. Run Grok as usual:

```sh
grok
```

Or use OpenCode in the same coding terminal:

```sh
opencode
```

These are ordinary shell commands, not dashboard-specific modes. Hiding/showing the coding terminal preserves whichever program you started.

Grok's full-screen mode is also available without altering your saved preferences:

```sh
grok --fullscreen
```

The dedicated tmux configuration enables full color, clipboard support, focus events, and modern key reporting. Development checks covered Grok 1.0.25's doctor and idle full-screen interface at widths of 60, 80, and 100 columns. Coding tools can change their terminal behavior; verify the version you install. Native Ghostty keyboard and paste behavior still requires a manual check on the target Mac.

The layout starts with equal columns. Enlarge the Ghostty window for comfortable space across all three; the legacy `--tmux` layout requires at least 90 columns and 24 rows when first opened. Ghostty's standard zoom shortcuts act on the focused column. Custom global `all:` zoom bindings in a personal Ghostty configuration would override that behavior; this installer does not change personal bindings.

## Process monitor

The right column keeps **btop's CPU and memory panels**, including their graphs and detailed readings, above the new process view. Only the network area is replaced. When the column is too narrow for btop (under 60 characters), compact CPU and memory graphs keep those readings visible; widening it restores the full btop view. Drag the horizontal divider to resize the resource graphs and process list. **CPU** ranks processes by CPU used during the latest sample; **Memory** ranks them by resident memory. Both show names, useful resource values, and process identifiers (PID). Narrow columns prioritize the selected resource; the selected process's PID remains below the table. Sampling runs every two seconds, and names can be filtered without losing keyboard focus.

Process CPU uses 100% for one core, so a process using several cores can exceed 100%. The overall CPU bar uses the full machine's 0–100% scale. Overall RAM comes from macOS memory counters, not a sum of process memory, which can double-count shared pages.

**GPU** ranks processes by measured GPU activity on Apple Silicon Macs. The collector reads the AGX driver's per-client `AppUsage` counters through a narrow, read-only `ioreg` query. It associates each client with its process, then compares GPU execution time between samples. The initial sample establishes a baseline before percentages appear. No administrator access, helper installation, or open Activity Monitor window is required. Process names are collected without full command arguments.

GPU percentages are GPU execution time divided by elapsed time, rather than a share of the entire chip. Concurrent work can produce values above 100%; values are not divided by core count or forced to add up to 100%. The displayed accumulated time covers currently reported GPU contexts, and can decrease when an app destroys a context. It is not a guaranteed lifetime total. Client identity, process start time, counter resets, and changes in the reported context list establish new baselines. The driver exposes no stable identifier for each context inside a client; replacement with an identical list shape can still affect the measured rate.

These are undocumented Apple driver properties. If an OS or hardware change makes them unavailable, the GPU tab reports that state instead of displaying fabricated measurements. The collector's GPU time was checked against Activity Monitor during development on an Apple Silicon Mac. The same access path is documented in this [independent implementation and measurement study](https://github.com/Zesty0wl/mac-performance-monitor/blob/main/docs/gpu-tab-design.md).

## Installation and files

This bundle is the editable source and test suite. To install or update it:

```sh
python3 install.py
```

Requires Homebrew's `tmux`, `btop`, `uv`, and Git. The installer places the command at `~/.local/bin/dashboard` and an independent copy of the app and its Python environment in `~/.local/share/ghostty-project-dashboard/`. Runtime layout settings live in `~/.local/state/ghostty-dashboard/`. The CPU/memory and process stack uses its own `ghostty-dashboard-resources` tmux server inside the right column; it leaves the outer layout and coding terminal unchanged.

Your Ghostty settings, regular tmux configuration, Grok configuration, and project files are not modified. The launcher uses a separate tmux server named `ghostty-dashboard`. Each project, including each linked Git worktree, gets its own persistent workspace.

To inspect only these dashboard sessions:

```sh
tmux -L ghostty-dashboard list-sessions
```

Native mode uses a controller session and three grouped viewing sessions per project. They share persistent windows, so closing a view does not stop its processes. The launching shell waits for its foreground dashboard attachment; Leave detaches that attachment and returns the shell. To deliberately end a project's work, exit its running programs first. Do not kill the whole dashboard server if other projects still have work running.

## Verification

From a clone, create the locked development environment and run the complete suite:

```sh
uv sync --locked --extra dev --python 3.13
uv run --locked --extra dev python -m unittest discover -s tests -v
uv build
```

The tests use disposable Git repositories and separate temporary tmux servers. They cover commit-persistent colors, all/committed totals, deleted and renamed files, binary files, filenames with spaces and terminal controls, linked worktrees, merge behavior, UI filters/previews, resizing, fixed left-pane order, installation payloads, and process survival through layout changes. The `dev` extra includes `pyte` for actual terminal mouse tests. Native Ghostty scripts were syntax-compiled against the installed app's scripting dictionary; their launch/focus/toggle/leave calls are mocked in tests. Launcher tests use private terminal devices to verify title markers, cleanup, and return-to-shell behavior.

**Verification boundary:** automated tests and script compilation do not prove a successful native window transition. During initial development, computer-use access to Ghostty was unavailable. Follow the manual acceptance checks in [the agent runbook](docs/AGENT_SETUP.md) before reporting native launch, Leave, or independent zoom as verified on a new Mac. GitHub Actions runs the headless macOS suite; it does not automate a Ghostty desktop.

Extremely large results produce an explicit message instead of incomplete totals: 5,000 changed files, 1,000 untracked files, 64 MiB of untracked text, or 2,000 branch commits. Diff previews are bounded to keep the interface responsive.

## More information

- [Installation and updates](docs/INSTALL.md)
- [Agent setup and acceptance checks](docs/AGENT_SETUP.md)
- [Architecture and configuration locations](docs/ARCHITECTURE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Upstream tools and references](THIRD_PARTY.md)

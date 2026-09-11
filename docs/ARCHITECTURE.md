# Architecture and configuration

## Runtime flow

`dashboard` resolves the Git root, creates or finds a persistent workspace, and selects native Ghostty mode unless `--tmux` is used.

The native launcher marks its invoking terminal with a random OSC 2 title, matches that exact title through Ghostty's scripting API, and stores stable surface/tab IDs. A single starting surface becomes the left column; the foreground launcher attaches a tmux client to it. Two new Ghostty surfaces host the center and right columns. If the starting tab already has unrelated splits, a separate dashboard tab is created in the same window.

Ghostty draws independently sized text in each column. tmux keeps processes alive independently of those surfaces. The left column contains two tmux panes; the right column contains a nested resource stack with btop above a Textual process table. The coding column is an ordinary persistent shell.

Leave closes only the recorded dashboard-created surfaces and detaches the recorded origin client. It returns the original shell. State generations prevent an older foreground launcher from cleaning up a replacement dashboard. Native toggling must preserve those origin/generation fields.

## Configuration sources

| Configuration | Source of truth |
| --- | --- |
| Main tmux settings, mouse controls, borders, footer | `cli.config_text()` |
| Project panes and comparison options | `cli.create_workspace()` |
| Native window/tab/surface actions | `native.py` |
| Native lifecycle and state persistence | `native_workspace.py` |
| btop black theme and CPU/memory selection | `resource_stack.THEME`, `resource_stack.BTOP_CONFIG` |
| Nested resource pane layout | `resource_stack.TMUX_CONFIG` |
| Narrow-column graph fallback | `resource_graphs.py` |
| Git/file panel layout and colors | `panels.py` |
| Process tabs, filtering, data colors | `processes.py` |

There is no hidden external configuration bundle. Do not copy a live state directory between Macs: it contains machine-specific terminal IDs, paths, and locks. The code regenerates it.

## Environment and installed paths

| Name | Default / purpose |
| --- | --- |
| `DASHBOARD_SOCKET` | `ghostty-dashboard`; primary tmux server |
| `DASHBOARD_RESOURCE_SOCKET` | `ghostty-dashboard-resources`; nested resource server |
| `DASHBOARD_STATE_DIR` | `~/.local/state/ghostty-dashboard`; generated state |
| `DASHBOARD_PROJECT` | Project root supplied to the workspace |
| `TERM_PROGRAM` | Must identify Ghostty for native launch from a normal shell |

Tests that start real monitor children must export **all three** socket/state overrides before starting them, and clean up only those test sockets. Changing Python module globals alone does not isolate spawned children.

`install.py` copies the allowlisted distributable payload into `~/.local/share/ghostty-project-dashboard/app`, installs the Python package into a sibling `venv`, and creates a marked launcher under `~/.local/bin`. The runtime installation resolves package dependencies through uv; `uv.lock` supplies an exact environment for contributor/CI checks.

## Git and resource data

`gitdata.py` computes a merge-base comparison against the selected main/master/default branch and combines committed changes with working-tree and untracked files for All mode. Committed mode excludes unfinished changes. Parent folders provide context; their colors never imply that every file inside changed. The collector is read-only and uses local refs.

`processdata.py` reads process CPU time/RSS and macOS system counters without full command arguments. CPU rates come from time differences between samples. `gpudata.py` reads a narrow AGX `ioreg` property list and differences counters by process start identity, registry client identity, and context-list shape. New/reset/changing contexts establish fresh baselines. Same-shape context replacement cannot be identified reliably by the undocumented driver fields.

GPU percentages represent execution time divided by elapsed time; they can exceed 100% and are not shares of a whole-chip total. Reported cumulative time covers currently reported contexts and may decrease when contexts disappear. Never silently clamp or replace unavailable GPU measurements.

## Important limits

- Native scripting is macOS/Ghostty-specific and currently assumes the standard application location.
- Ghostty's API cannot read font size or split widths. Recreated center surfaces reset zoom and equalize columns.
- Native column positions cannot be locked through this scripting API; the two left tmux panes are fixed.
- Fixed Ghostty titles block origin-marker lookup; failure must never fall back to guessing the frontmost terminal.
- Unit tests mock native actions. Desktop transitions require the manual acceptance checks.

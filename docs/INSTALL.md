# Installation

## Requirements

- macOS with Ghostty 1.3 or newer installed at `/Applications/Ghostty.app`.
- Homebrew, Git, tmux, btop, and uv available in the shell's PATH.
- Python 3.11 or newer. The quick start selects Python 3.13 through uv so it does not depend on Apple's bundled Python.
- A local Git project for the dashboard to inspect.
- Compatible Apple Silicon graphics drivers for per-process GPU measurements. Other hardware can still show CPU/memory; unsupported GPU counters are explicitly unavailable.

Development verification used Ghostty 1.3.1, tmux 3.6b, btop 1.4.7, Textual 8.2.8, and Python 3.14.2. CI uses Python 3.13. These are verified development versions, not a promise that every older version works.

## Fresh Mac

Install [Homebrew](https://brew.sh) first if needed. Follow its displayed shell setup instructions, then:

```sh
brew install --cask ghostty
brew install git tmux btop uv node
git clone https://github.com/santiagovelasquezt2/ghostty-project-dashboard.git
cd ghostty-project-dashboard
uv run --no-project --python 3.13 python install.py
export PATH="$HOME/.local/bin:$PATH"
dashboard --version
```

If Git is already installed, you can instead clone first and use `brew bundle --file=Brewfile` to install the same tools. The [Ghostty installation guide](https://ghostty.org/docs/install/binary) also offers the official app download. uv can [install the selected Python version](https://docs.astral.sh/uv/guides/install-python/) automatically.

To persist the command in zsh, add this line once to `~/.zshrc`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer prints this advice but does not edit your shell startup files. If another unrelated command already occupies `~/.local/bin/dashboard`, installation stops instead of overwriting it.

## Start it

In a **normal Ghostty terminal**, outside an existing tmux session:

```sh
cd /path/to/your/git-project
dashboard
```

You can also pass a folder or file inside a Git project. macOS may ask for permission to automate Ghostty; allow it for the terminal/launcher if you want native layouts. No admin access or coding-assistant credentials are required by the dashboard itself.

The starting window becomes the dashboard. Click **Leave**, or press **Ctrl-b**, then **d**, to return to the original shell. Coding programs remain running in the private tmux session. See the [manual acceptance checks](AGENT_SETUP.md#manual-acceptance-checks).

The `--tmux` option runs all sections in one terminal surface. It avoids native scripting, but font zoom affects all sections. It still uses macOS-specific process collectors; this is not a Linux compatibility mode.

## What gets installed

| Path | Contents |
| --- | --- |
| `~/.local/bin/dashboard` | Managed launcher |
| `~/.local/share/ghostty-project-dashboard/app` | Independent source, docs, and tests |
| `~/.local/share/ghostty-project-dashboard/venv` | Runtime Python environment |
| `~/.local/state/ghostty-dashboard` | Generated tmux/btop settings, locks, and native surface state |

The installer adds one `config-file` include to the macOS Ghostty config for default Command editing shortcuts. These specific editing combinations are remapped throughout Ghostty; other preferences remain intact. Its prior config is backed up privately under the installed app directory. `.tmux.conf` and coding-assistant preferences are not modified. The repository contains the generators for the entire dashboard configuration, so another computer does not need these runtime files from the original installation.

## Update

From your clean repository checkout:

```sh
git pull --ff-only
uv run --no-project --python 3.13 python install.py
dashboard --version
```

The installed app is not an editable link to the clone. Re-run the installer after source updates. New launcher invocations use the installed version; already running panels retain loaded code until restarted. Preserve coding sessions when refreshing read-only panels, or save and exit coding programs before deliberately restarting a complete workspace. Do not terminate every dashboard just to refresh one project.

## Remove

First save work and exit coding programs in **every** dashboard you intend to stop. Leave only detaches views; it does not terminate those programs. If you want to remove all dashboard sessions, explicitly stop both dashboard-only tmux servers:

```sh
tmux -L ghostty-dashboard kill-server
tmux -L ghostty-dashboard-resources kill-server
```

Those commands terminate all dashboard sessions, not ordinary tmux sessions. A “no server running” message means that server is already stopped.

Then remove the managed launcher and app directory. Verify the launcher still contains `# Managed by Ghostty Project Dashboard` before deleting it, in case you later replaced that command with a different tool. The state directory may also be removed after the sessions are stopped. Remove the Ghostty Project Dashboard `config-file` include and its preceding comment from the macOS Ghostty config before deleting the app directory, then reload Ghostty settings. Removing the include restores the previous editing bindings.

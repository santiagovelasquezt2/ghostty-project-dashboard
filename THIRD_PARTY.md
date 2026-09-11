# Upstream tools and references

The dashboard invokes separately installed tools and uses Python dependencies. It does not bundle their binaries, another application's configuration, or the originating user's dotfiles. Each upstream project retains its own license and notices.

- [Ghostty](https://github.com/ghostty-org/ghostty): terminal rendering and native macOS scripting. The installed `Ghostty.sdef` is the source for supported scripting commands.
- [tmux](https://github.com/tmux/tmux): persistent pane processes and mouse/keyboard routing.
- [btop](https://github.com/aristocratos/btop): full CPU and memory graphs.
- [Textual](https://github.com/Textualize/textual): custom Git/file/process interfaces.
- [pyte](https://github.com/selectel/pyte): development-only terminal parser for tests.
- [uv](https://github.com/astral-sh/uv): Python and package environment management.
- [Git](https://git-scm.com): local repository observation.
- [AGX process GPU measurement study](https://github.com/Zesty0wl/mac-performance-monitor/blob/main/docs/gpu-tab-design.md): reference for the unprivileged GPU counter access path and its measurement limits.

LazyGit, Yazi, and Herdr inspired discussion of possible layouts but are not required by this implementation.

- [Prettier](https://prettier.io/) provides in-memory preview formatting through its API; its pinned npm dependency retains its upstream license.

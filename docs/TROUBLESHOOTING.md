# Troubleshooting

| Symptom | Check / remedy |
| --- | --- |
| `dashboard: command not found` | Add `~/.local/bin` to PATH, open a new shell, or invoke `~/.local/bin/dashboard --version`. |
| Python version error | Use `uv run --no-project --python 3.13 python install.py`. Apple's bundled `python3` may be older. |
| Missing tmux/btop/uv/Git | Run `brew bundle --file=Brewfile` from the clone, then ensure Homebrew's shell setup is active. |
| Native launch cannot identify the terminal | Start in a normal foreground Ghostty shell. Check for a fixed `title` in Ghostty configuration; that prevents the temporary title marker. Do not target a different window by guessing. |
| Automation denied | Check macOS System Settings → Privacy & Security → Automation for the terminal/launcher and Ghostty. A managed Mac may restrict this. `--tmux` avoids native scripting but shares one font size. |
| An automation tool says Ghostty is blocked | Respect that restriction. Install and test the code normally, then use the manual desktop checklist; do not bypass it through shell UI automation. |
| Existing tmux session rejected | Run native `dashboard` outside unrelated tmux. Re-entry from a dashboard coding pane only focuses an existing native layout. |
| Font changes resize every section | Confirm you are using native mode, focus the coding column, and check for custom Ghostty `all:` font bindings. `--tmux` necessarily shares its font size. |
| Old separate dashboard window remains | Run the installed command again from the original normal Ghostty terminal. It can retire recorded older dashboard surfaces after preparing the replacement. It does not close unrelated windows. |
| A saved surface was moved to another tab | Restore that surface to its dashboard tab before toggling/leaving. Ownership checks deliberately avoid closing a moved or unrelated terminal. |
| Changes remain after a remote merge | Local refs may not know about the merge. Update refs using your normal Git workflow. Squash/rebase merges cannot always be recognized by ancestry; a fresh branch from updated main is the clean baseline. |
| GPU shows unavailable | The AGX properties may be unsupported on the hardware/OS. CPU/memory still work; root access is not the intended fix. |
| GPU shows dashes briefly | Allow two samples, approximately four seconds. New or changing graphics contexts need a baseline. |
| GPU percentage exceeds 100% | Concurrent GPU execution time can exceed wall-clock time. It is not a whole-chip usage share. |
| btop changes to simpler graphs | The upper right pane is below btop's size threshold. Make the pane wider or taller to restore btop; the compact view keeps CPU and memory visible. |
| An edit in the clone has no effect | The installed app is an independent copy. Re-run `install.py`; already running panel processes keep their loaded code until refreshed. |
| Leave did not end Grok/OpenCode | Expected: Leave preserves the coding session. Exit the coding tool explicitly if you want to stop it. |

Useful read-only checks:

```sh
dashboard --version
tmux -V
btop --version
uv --version
tmux -L ghostty-dashboard list-sessions
```

Do not post raw session state, process command arguments, credentials, or screenshots of private work in issues. Include the dashboard/Ghostty versions, Mac architecture, the exact error, and steps using a disposable project.

"""Ordinary Command editing bindings, with no key-table activation or pill."""
from pathlib import Path

# Standard editing equivalents: Ctrl-_ for undo, never Ctrl-Z (suspend).
# Applications outside Notes retain control over what these editing keys do.
KEYS = (
    ('super+z', '95;5u'),
    ('super+shift+z', '121;5u'),
    ('super+a', '97;5u'),
    ('super+shift+arrow_left', '1;2H'),
    ('super+shift+arrow_right', '1;2F'),
    ('super+shift+arrow_up', '1;6H'),
    ('super+shift+arrow_down', '1;6F'),
    ('super+alt+shift+arrow_left', '1;6D'),
    ('super+alt+shift+arrow_right', '1;6C'),
)
CONFIG = '\n'.join(f'keybind = {key}=csi:{sequence}' for key,sequence in KEYS) + '\n'


def install(home: Path, destination: Path):
    """Keep personal settings intact; the managed include supplies editing overrides."""
    table = destination / 'notes-keybindings.conf'
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text(CONFIG)
    # Only the managed include changes; preserve the rest of the user config.
    config = home / 'Library/Application Support/com.mitchellh.ghostty/config'
    config.parent.mkdir(parents=True, exist_ok=True)
    directive = 'config-file = ' + str(table)
    previous = config.read_text() if config.exists() else ''
    if directive not in previous.splitlines():
        if config.exists():
            backup = destination / 'ghostty-config-before-notes.conf'
            if not backup.exists(): backup.write_text(previous)
        with config.open('a') as file:
            file.write('\n# Ghostty Project Dashboard: Command editing shortcuts\n' + directive + '\n')

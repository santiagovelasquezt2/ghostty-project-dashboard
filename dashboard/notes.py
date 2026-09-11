"""Editable project notes with decorative bullets and independent persistence."""
import hashlib
import os
from pathlib import Path
import tempfile
from rich.segment import Segment
from rich.style import Style
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.strip import Strip
from textual.widgets import TextArea, Static


def plain_notes(text):
    return '\n'.join(line[2:] if line.startswith(('• ', '- ', '* ')) else line
                     for line in text.replace('\r\n','\n').replace('\r','\n').split('\n'))

def bullets(text):
    return '\n'.join('• ' + line for line in text.split('\n')) if text else ''


class BulletEditor(TextArea):
    BINDINGS = [
        Binding("ctrl+z,ctrl+underscore,super+z", "undo", show=False),
        Binding("ctrl+shift+z,super+shift+z,shift+super+z", "redo", show=False),
        Binding("ctrl+a,super+a", "select_all", show=False),
        Binding("super+shift+left,shift+super+left,shift+home", "select_boundary('line_start')", show=False),
        Binding("super+shift+right,shift+super+right,shift+end", "select_boundary('line_end')", show=False),
        Binding("super+shift+up,shift+super+up,ctrl+shift+home,shift+ctrl+home", "select_boundary('start')", show=False),
        Binding("super+shift+down,shift+super+down,ctrl+shift+end,shift+ctrl+end", "select_boundary('end')", show=False),
        Binding("alt+shift+right,shift+alt+right,super+alt+shift+right,shift+alt+super+right", "cursor_word_right(True)", show=False),
        Binding("alt+shift+left,shift+alt+left,super+alt+shift+left,shift+alt+super+left", "cursor_word_left(True)", show=False),
    ]
    def action_select_boundary(self, boundary):
        row, _ = self.cursor_location
        lines = self.document.lines
        targets = {
            'line_start': (row, 0),
            'line_end': (row, len(lines[row])),
            'start': (0, 0),
            'end': (len(lines)-1, len(lines[-1])),
        }
        self.move_cursor(targets[boundary], select=True)

    # The gutter participates in TextArea's wrapping and mouse-coordinate math.
    # Bullets never enter the document or its undo history.
    @property
    def gutter_width(self):
        return 3

    def render_line(self, y):
        strip = super().render_line(y)
        offset = y + self.scroll_offset.y
        info = self.wrapped_document._offset_to_line_info
        marker = ' • ' if self.text and offset < len(info) and info[offset][1] == 0 else '   '
        return Strip.join([Strip([Segment(marker, Style(color='#888888',bgcolor='#000000'))]), strip.crop(3)])

    async def _on_paste(self, event):
        event.stop()
        event.prevent_default()
        event.text = plain_notes(event.text)
        await super()._on_paste(event)


class Notes(Vertical):
    DEFAULT_CSS = '''
    Notes { height: 1fr; min-height: 3; background: #000000; padding: 0 1; }
    Notes TextArea { height: 1fr; min-height: 1; background: #000000; color: #cccccc; border: none; }
    #notes-status { height: 1; color: #888888; text-wrap: nowrap; text-overflow: ellipsis; }
    '''
    def __init__(self, root, **kwargs):
        super().__init__(**kwargs)
        state=Path(os.environ.get('DASHBOARD_STATE_DIR',str(Path.home()/'.local/state/ghostty-dashboard')))
        key=hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:24]
        self.path=state/'notes'/f'{key}.txt'
        self.save_enabled=True
        try: self.disk_text=self.path.read_text()
        except FileNotFoundError: self.disk_text=None
        except (OSError,UnicodeError): self.disk_text=None; self.save_enabled=False
        self.initial=plain_notes(self.disk_text or '')
        self.last_saved=self.initial
        self.save_timer=None

    def compose(self) -> ComposeResult:
        yield Static('NOTES · saved automatically' if self.save_enabled else 'Cannot read saved notes · saving disabled',id='notes-status',markup=False)
        yield BulletEditor(self.initial,id='notes-editor',show_line_numbers=True,soft_wrap=True)

    @on(TextArea.Changed,'#notes-editor')
    def changed(self,event):
        if self.save_timer: self.save_timer.stop()
        self.save_timer=self.set_timer(0.2,self.save)

    def save(self):
        text=self.query_one(TextArea).text
        if text==self.last_saved or not self.save_enabled: return
        temporary=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            current=self.path.read_text() if self.path.exists() else None
            if current != self.disk_text:
                self.query_one('#notes-status',Static).update('Notes changed elsewhere · copy your edits before reopening')
                self.save_enabled=False
                return
            serialized=bullets(text)
            with tempfile.NamedTemporaryFile(mode='w',dir=self.path.parent,delete=False) as file:
                temporary=Path(file.name)
                file.write(serialized)
            temporary.replace(self.path)
            self.disk_text=serialized
            self.last_saved=text
            self.query_one('#notes-status',Static).update('Saved')
        except (OSError,UnicodeError):
            self.query_one('#notes-status',Static).update('Could not save · copy to keep your edits')
        finally:
            if temporary: temporary.unlink(missing_ok=True)


class NotesApp(App):
    CSS='Screen { background: #000000; } Notes { height: 1fr; }'
    def __init__(self,root):
        super().__init__()
        self.root=root
    def compose(self): yield Notes(self.root)
    def on_mount(self): self.query_one(TextArea).focus()
    def action_quit(self):
        self.query_one(Notes).save()
        super().action_quit()

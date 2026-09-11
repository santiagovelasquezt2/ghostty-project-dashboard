"""Editable project notes with decorative bullets and independent persistence."""
import asyncio
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from rich.segment import Segment
from rich.style import Style
from textual import on, events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Grid
from textual.strip import Strip
from textual.widgets import TextArea, Button, Static


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


class NoteButton(Button):
    can_focus = False
    def __init__(self,*args,**kwargs):
        kwargs.setdefault("compact", True)
        super().__init__(*args,**kwargs)
        self.active_effect_duration=0


def resize_notes(amount):
    from . import cli, native_workspace, native
    source=os.environ.get('TMUX_PANE')
    if not source: raise RuntimeError('Notes is not running inside a dashboard pane.')
    session=cli.tmux('display-message','-p','-t',source,'#{session_id}')
    session=native_workspace.controller(session)
    if cli.option(session,'notes') != source:
        raise RuntimeError('The notes pane no longer belongs to this workspace.')
    if cli.option(session,'backend') == 'native':
        with cli.locked(session+'-native'):
            state=native_workspace.read_state(session)
            if not state or not state.get('terminals',{}).get('top'):
                raise RuntimeError('Notes section is no longer visible.')
            native.resize(state,'top','down' if amount>0 else 'up',abs(amount))
    else:
        pane=cli.option(session,'notes')
        if not pane: raise RuntimeError('No dashboard notes pane.')
        width=int(cli.tmux('display-message','-p','-t',pane,'#{pane_height}'))
        cli.tmux('resize-pane','-t',pane,'-y',str(max(6,width+(4 if amount>0 else -4))))


class Notes(Vertical):
    DEFAULT_CSS = '''
    Notes { height: 1fr; min-height: 3; background: #000000; padding: 0 1; }
    Notes TextArea { height: 1fr; min-height: 1; background: #000000; color: #cccccc; border: none; }
    #notes-actions { height: 1; grid-size: 3 1; grid-columns: 1fr 1fr 1fr; grid-rows: 1; }
    #notes-actions.narrow { height: 2; grid-size: 2 2; grid-columns: 1fr 1fr; grid-rows: 1 1; }
    #notes-width { height: 1; grid-size: 2 1; grid-columns: 1fr 1fr; grid-rows: 1; }
    Notes Button { height: 1; min-width: 0; width: 1fr; padding: 0; border: none; margin: 0; background: #202020; color: #bbbbbb; }
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
        self.resize_running=False

    def compose(self) -> ComposeResult:
        yield Static('NOTES · saved automatically' if self.save_enabled else 'Cannot read saved notes · saving disabled',id='notes-status',markup=False)
        yield BulletEditor(self.initial,id='notes-editor',show_line_numbers=True,soft_wrap=True)
        with Grid(id='notes-actions'):
            yield NoteButton('Copy',id='notes-copy',tooltip='Copy selection, or all bullets')
            yield NoteButton('Paste',id='notes-paste')
            yield NoteButton('Delete',id='notes-delete',tooltip='Delete current bullet or selected bullets · Undo restores it')
        with Grid(id='notes-width'):
            yield NoteButton('− Height',id='notes-narrow',tooltip='Make the top section shorter')
            yield NoteButton('+ Height',id='notes-widen',tooltip='Make the top section taller')

    def on_resize(self, event):
        if self.is_mounted:
            self.query_one('#notes-actions').set_class(event.size.width < 28, 'narrow')

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

    @on(Button.Pressed,'#notes-delete')
    def delete_note(self):
        editor=self.query_one(TextArea)
        start,end=sorted(editor.selection)
        first,last=start[0],end[0]
        if end[1]==0 and last>first: last-=1
        lines=editor.text.split('\n')
        if last<len(lines)-1: a,b=(first,0),(last+1,0)
        elif first>0: a,b=(first-1,len(lines[first-1])),(last,len(lines[last]))
        else: a,b=(0,0),(last,len(lines[last]))
        editor.replace('',a,b,maintain_selection_offset=False)
        editor.focus()
        self.save()

    @on(Button.Pressed,'#notes-copy')
    async def copy_notes(self):
        editor=self.query_one(TextArea)
        selected=editor.selected_text
        text=bullets(selected) if '\n' in selected else selected or bullets(editor.text)
        try:
            await asyncio.to_thread(subprocess.run,['/usr/bin/pbcopy'],input=text,text=True,check=True,timeout=2)
            self.query_one('#notes-status',Static).update('Copied selection' if selected else 'Copied all bullets')
        except (OSError,subprocess.SubprocessError):
            self.query_one('#notes-status',Static).update('Clipboard unavailable')

    @on(Button.Pressed,'#notes-paste')
    async def paste_notes(self):
        editor=self.query_one(TextArea)
        selection=editor.selection
        original=editor.text
        try:
            result=await asyncio.to_thread(subprocess.run,['/usr/bin/pbpaste'],text=True,capture_output=True,check=True,timeout=2)
            if editor.text != original or editor.selection != selection:
                self.query_one('#notes-status',Static).update('Selection changed · paste again')
                return
            editor.replace(plain_notes(result.stdout),*sorted(selection),maintain_selection_offset=False)
            editor.focus()
        except (OSError,subprocess.SubprocessError):
            self.query_one('#notes-status',Static).update('Clipboard unavailable')

    @on(Button.Pressed,'#notes-narrow')
    def narrow(self): self.change_width(-64)

    @on(Button.Pressed,'#notes-widen')
    def widen(self): self.change_width(64)

    @work(exit_on_error=False)
    async def change_width(self,amount):
        if self.resize_running: return
        self.resize_running=True
        try: await asyncio.to_thread(resize_notes,amount)
        except Exception:
            self.query_one('#notes-status',Static).update('Could not resize · use the Ghostty divider')
        finally:
            self.resize_running=False


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

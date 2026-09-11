"""Send the exact configured bytes through a real disposable tmux + Textual."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
import uuid
from dashboard import cli
from test_status_click import RecordingScreen
import pyte

class NotesTransportTests(unittest.TestCase):
    def test_shortcuts_through_tmux(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); state=root/'editor.json'
            script=root/'fixture.py'
            script.write_text('''import json,sys,time
# Exercise slow startup deterministically instead of depending on runner speed.
time.sleep(.25)
from pathlib import Path
from dashboard.notes import NotesApp
from textual.widgets import TextArea
from textual.binding import Binding
class Fixture(NotesApp):
 BINDINGS=[Binding('f12','ack',show=False,priority=True)]
 acks=0
 def action_ack(self):
  self.acks+=1
  self.snapshot()
 def on_mount(self):
  super().on_mount()
  self.set_interval(.03,self.snapshot)
 def snapshot(self):
  e=self.query_one(TextArea)
  Path(sys.argv[1]).write_text(json.dumps({'text':e.text,'selected':e.selected_text,'acks':self.acks}))
Fixture('/tmp/fixture').run()
''')
            socket='dashboard-notes-test-'+uuid.uuid4().hex[:12]
            config=root/'tmux.conf';config.write_text(cli.config_text())
            env=os.environ.copy();env.pop('TMUX',None);env.pop('TMUX_PANE',None)
            env.update(TERM='xterm-256color',DASHBOARD_STATE_DIR=str(root),PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            import shlex
            command=shlex.join([sys.executable,str(script),str(state)])
            subprocess.run(['tmux','-L',socket,'-f',str(config),'new-session','-d','-s','notes','-x','80','-y','24',command],env=env,check=True)
            # Do not answer the outer terminal's device queries until Textual
            # has entered raw input mode. Otherwise replies can become editor
            # text when the fixture starts slowly on a fresh runner.
            deadline=time.monotonic()+10
            while not state.exists():
                if time.monotonic()>=deadline:
                    subprocess.run(['tmux','-L',socket,'kill-server'],capture_output=True)
                    self.fail('Notes fixture did not finish mounting')
                time.sleep(.02)
            pid,master=pty.fork()
            if pid==0:
                fcntl.ioctl(0,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,0,0))
                os.execve(shutil.which('tmux'),['tmux','-L',socket,'attach','-t','notes'],env)
            screen=RecordingScreen(80,24);stream=pyte.ByteStream(screen)
            screen.writer=lambda data:os.write(master,data)
            expected_ack=0
            def expect(text=None,selected=None):
                end=time.monotonic()+5
                while time.monotonic()<end:
                    if select.select([master],[],[],.03)[0]: stream.feed(os.read(master,65536))
                    try: data=json.loads(state.read_text())
                    except (OSError,ValueError): continue
                    if data['acks']>=expected_ack and (text is None or data['text']==text) and (selected is None or data['selected']==selected):return
                self.fail('Unexpected fixture editor state: '+str(data if state.exists() else 'not started'))
            def send(data,**expected):
                nonlocal expected_ack
                expected_ack+=1
                os.write(master,data+b'\x1b[24~')
                expect(**expected)
            try:
                expect(text='')
                # Drain startup device queries before exercising editor input.
                startup_deadline=time.monotonic()+.6
                while time.monotonic()<startup_deadline:
                    if select.select([master],[],[],.03)[0]: stream.feed(os.read(master,65536))
                send(b'\x1b[18~\x7f',text='')
                send(b'first second',text='first second')
                send(b'\x1a',text='')
                send(b'\x19',text='first second')
                send(b'\x1b[18~',selected='first second')
                send(b'\x1b[C',selected='')
                send(b'\x1b[1;2D',selected='d')
                send(b'\x1b[18~',selected='first second')
                send(b'\x1b[D',selected='')
                send(b'\x1b[1;2C',selected='f')
                send(b'\x1b[18~',selected='first second')
                send(b'\x1b[C',selected='')
                send(b'\x1b[1;6D',selected='second')
                send(b'\x1b[18~',selected='first second')
                send(b'first\rsecond\rthird',text='first\nsecond\nthird')
                send(b'\x1b[1;2H',selected='third')
                send(b'\x1b[1;2F',selected='')
                send(b'\x1b[1;6H',selected='first\nsecond\nthird')
                send(b'\x1b[1;6F',selected='')
                # Default Ghostty bindings send standard editing keys; no key table.
                send(b'\x1b[97;5u',selected='first\nsecond\nthird')
                send(b'new text',text='new text')
                send(b'\x1b[95;5u',text='n')
                send(b'\x1b[95;5u',text='first\nsecond\nthird')
                send(b'\x1b[121;5u',text='n')
                send(b'\x1b[121;5u',text='new text')
                send(b'\x1b[1;2H',selected='new text')
                send(b'\x1b[1;2F',selected='')
                send(b'\x1b[1;6D',selected='text')
                send(b'\x1b[1;6C',selected='')
                send(b'\x1b[97;5u',selected='new text')
                send(b'first\rsecond\rthird',text='first\nsecond\nthird')
                send(b'\x1b[1;6H',selected='first\nsecond\nthird')
                send(b'\x1b[1;6F',selected='')
            finally:
                subprocess.run(['tmux','-L',socket,'kill-server'],capture_output=True)
                os.close(master)
                if not os.waitpid(pid,os.WNOHANG)[0]:
                    os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)

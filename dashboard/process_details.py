"""Review and explicitly copy process context without interrupting the process."""
import asyncio
import subprocess
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static, TextArea
from .process_report import collect_report


class ProcessTable(DataTable):
    class Inspect(Message):
        def __init__(self, pid: int):
            super().__init__()
            self.pid = pid

    async def _on_click(self, event: events.Click) -> None:
        row = event.style.meta.get('row', -1)
        if event.chain == 2 and row >= 0 and not event.style.meta.get('out_of_bounds') and self.is_valid_row_index(row):
            self.post_message(self.Inspect(int(self.ordered_rows[row].key.value)))
            event.stop()
            return
        await super()._on_click(event)


class ProcessDetails(ModalScreen):
    DEFAULT_CSS = '''
    ProcessDetails { align: center middle; background: #000000 70%; }
    #process-details { width: 96%; height: 95%; background: #000000; border: solid #444444; padding: 0 1; }
    #report-title { height: 1; color: #dddddd; text-style: bold; }
    #process-report { height: 1fr; background: #000000; color: #b8b8b8; border: none; }
    #report-status { height: auto; color: #999999; }
    #report-actions { height: 3; }
    #report-actions Button { min-width: 7; width: 1fr; background: #202020; color: #dddddd; }
    '''
    BINDINGS = [Binding('escape', 'close', 'Close'), Binding('b', 'close', 'Back')]

    def __init__(self, process):
        super().__init__()
        self.process = process
        self.report = ''

    def compose(self) -> ComposeResult:
        with Vertical(id='process-details'):
            yield Static(f'PROCESS · PID {self.process.pid}', id='report-title', markup=False)
            yield TextArea('Reading process details…', read_only=True, id='process-report', show_line_numbers=False)
            yield Static('Review before sharing. Common credentials are redacted.', id='report-status', markup=False)
            with Horizontal(id='report-actions'):
                yield Button('Copy', id='copy-report', disabled=True)
                yield Button('Close', id='close-report')

    def on_mount(self):
        self.load_report()

    @work(exclusive=True)
    async def load_report(self):
        try:
            self.report = await asyncio.to_thread(collect_report, self.process)
        except Exception:
            self.query_one('#report-status', Static).update('Could not read this process. Close and try again.')
            return
        if self.is_mounted:
            self.query_one(TextArea).load_text(self.report)
            self.query_one('#copy-report', Button).disabled = False

    @on(Button.Pressed, '#copy-report')
    async def copy_report(self):
        try:
            result = await asyncio.to_thread(subprocess.run, ['/usr/bin/pbcopy'], input=self.report, text=True, capture_output=True, timeout=3)
            if result.returncode:
                raise OSError('Clipboard unavailable')
        except (OSError, subprocess.TimeoutExpired):
            self.query_one('#report-status', Static).update('Clipboard unavailable. Select the report text to copy manually.')
        else:
            self.query_one('#report-status', Static).update('Copied process report.')

    @on(Button.Pressed, '#close-report')
    def action_close(self):
        self.dismiss()

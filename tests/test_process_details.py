import unittest
from unittest.mock import patch
from subprocess import CompletedProcess
from textual.widgets import Button, Static, DataTable
from dashboard.process_report import collect_report, redact
from dashboard.process_details import ProcessDetails
from dashboard.processes import ProcessMonitor
from test_processes import sample

class ReportTests(unittest.TestCase):
    def test_report_and_redaction(self):
        def run(*args):
            if args[0].endswith('lsof'): return 'COMMAND PID NAME\napp 100 /tmp/example'
            return {'lstart=':'Fri Sep 11 12:00:00 2026', 'comm=':'/Applications/Example.app/Contents/MacOS/app', 'ppid=':'1', 'command=':'app --token=secret-value --port 8080'}.get(args[-1],'value')
        with patch('dashboard.process_report._run',side_effect=run):
            report=collect_report(sample().processes[0])
        self.assertIn('Application bundle: /Applications/Example.app',report)
        self.assertIn('142.50%',report)
        self.assertIn('Network connections',report)
        self.assertNotIn('secret-value',report)
        self.assertIn('--port 8080',report)
        self.assertNotIn('hunter2',redact('app --password "hunter2"'))

    def test_exited_and_replaced_process(self):
        with patch('dashboard.process_report._run',return_value=None):
            self.assertIn('exited',collect_report(sample().processes[0]))
        starts=iter(['old','new'])
        with patch('dashboard.process_report._run',side_effect=lambda *a: next(starts) if a[-1]=='lstart=' else '100'):
            self.assertIn('changed',collect_report(sample().processes[0]))

class DetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_double_click_copy_and_memory_d(self):
        app=ProcessMonitor(auto_refresh=False)
        with patch('dashboard.process_details.collect_report',return_value='Agent report: example'), patch('dashboard.process_details.subprocess.run',return_value=CompletedProcess([],0)) as copied:
            async with app.run_test(size=(60,25)) as pilot:
                app.apply_snapshot(sample())
                await pilot.press('d')
                self.assertEqual(app.metric_name,'memory')
                await pilot.pause()
                await pilot.click('#process-table',offset=(3,1),times=2)
                await pilot.pause()
                self.assertIsInstance(app.screen,ProcessDetails)
                await app.workers.wait_for_complete()
                await pilot.click('#copy-report')
                await pilot.pause()
                self.assertEqual(copied.call_args.kwargs['input'],'Agent report: example')
                self.assertIn('Copied',str(app.screen.query_one('#report-status',Static).render()))
                await pilot.press('escape')
                self.assertFalse(app._details_open)
                self.assertEqual(app.query_one(DataTable).row_count,3)

import unittest
from io import StringIO
from rich.console import Console
from textual.widgets import RichLog, Button
from dashboard.split_diff import split_diff
from dashboard.panels import DashboardPanel

RAW = '--- a/example.tsx\n+++ b/example.tsx\n@@ -10,3 +10,4 @@\n context\n-old value\n+new value\n+extra value\n tail\n'

class SplitTests(unittest.TestCase):
    def render(self, raw=RAW, width=90):
        out=StringIO(); Console(file=out,width=width,color_system=None).print(split_diff(raw,'example.tsx',width),soft_wrap=True)
        return out.getvalue()

    def test_pairs_replacements_and_aligns_context_after_unequal_changes(self):
        text=self.render(); rows=text.splitlines()
        self.assertIn('Before: lines 10–12 → After: lines 10–13',text)
        replacement=next(row for row in rows if 'old value' in row)
        self.assertIn('new value',replacement)
        extra=next(row for row in rows if 'extra value' in row)
        self.assertNotIn('old value',extra)
        tail=next(row for row in rows if 'tail' in row)
        self.assertEqual(tail.count('tail'),2)
        self.assertIn('12',tail); self.assertIn('13',tail)

    def test_long_code_stays_on_one_row_without_losing_text(self):
        raw='@@ -1 +1 @@\n-'+('oldword '*22)+'\n+'+('newword '*22)+'\n'
        text=self.render(raw,80)
        self.assertEqual(text.count('oldword'),22)
        self.assertEqual(text.count('newword'),22)
        body=[row for row in text.splitlines() if 'oldword' in row]
        self.assertEqual(len(body),1)
        self.assertIn('newword '*22,body[0])
        self.assertGreater(len(body[0]),80)

    def test_add_delete_and_non_text(self):
        for raw in ('@@ -0,0 +1 @@\n+added\n','@@ -1 +0,0 @@\n-deleted\n','Binary files differ'):
            text=self.render(raw)
            self.assertIn('added' if '+added' in raw else 'deleted' if '-deleted' in raw else 'Binary files differ',text)

class SplitPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_toggle_resize_and_controls(self):
        app=DashboardPanel('files','/tmp',auto_refresh=False)
        async with app.run_test(size=(90,25)) as pilot:
            app._preview_open=True; app._preview_raw=RAW; app._preview_path='example.tsx'
            app.query_one('#preview').display=True; app._update_controls(); app.render_preview()
            await pilot.pause(); await pilot.click('#split-preview'); await pilot.pause()
            self.assertTrue(app.split_preview)
            for width in (24,40,100,28):
                await pilot.resize_terminal(width,25); await pilot.pause()
                buttons=[app.query_one('#'+name,Button) for name in ('open-entry','format-preview','split-preview','refresh')]
                for button in buttons:
                    self.assertGreater(button.region.width,0)
                    self.assertLessEqual(button.region.right,width)
                for i,button in enumerate(buttons):
                    for other in buttons[i+1:]: self.assertFalse(button.region.overlaps(other.region))
            await pilot.click('#split-preview');self.assertFalse(app.split_preview)
            self.assertEqual(app._preview_raw,RAW)

    async def test_long_split_lines_scroll_horizontally_without_wrapping(self):
        app=DashboardPanel('files','/tmp',auto_refresh=False)
        async with app.run_test(size=(60,25)) as pilot:
            app._preview_open=True; app.split_preview=True
            app._preview_raw='@@ -1 +1 @@\n-'+('oldword '*22)+'\n+'+('newword '*22)+'\n'
            app.query_one('#preview').display=True;app._update_controls();app.render_preview()
            await pilot.pause()
            preview=app.query_one(RichLog)
            self.assertGreater(preview.virtual_size.width,preview.size.width)
            self.assertEqual(sum('oldword' in line.text for line in preview.lines),1)
            preview.scroll_right(animate=False);await pilot.pause()
            self.assertGreater(preview.scroll_x,0)
            before=preview.virtual_size.height
            await pilot.resize_terminal(100,25);await pilot.pause()
            self.assertEqual(preview.virtual_size.height,before)

    async def test_added_file_uses_full_width_and_retains_split_preference(self):
        from dashboard.panels import Entry
        from dashboard.gitdata import FileChange
        app=DashboardPanel('files','/tmp',auto_refresh=False)
        async with app.run_test(size=(80,25)) as pilot:
            app._preview_open=True;app.split_preview=True
            app._preview_entry=Entry('new.ts',change=FileChange('new.ts','A'))
            # Formatted previews have a regular a/path header even for new files.
            app._preview_raw='--- a/new.ts\n+++ b/new.ts\n@@ -0,0 +1 @@\n+const added = true;\n'
            app._preview_path='new.ts'
            app.query_one('#preview').display=True;app.render_preview();await pilot.pause()
            preview=app.query_one(RichLog)
            line=next(line.text for line in preview.lines if 'const added' in line.text)
            self.assertTrue(line.startswith('+const added'))
            self.assertNotIn('│',line)
            self.assertEqual(preview.scroll_x,0)
            self.assertTrue(app.query_one('#split-preview',Button).disabled)
            self.assertTrue(app.split_preview)
            app._preview_entry=Entry('existing.ts',change=FileChange('existing.ts','M'))
            app._preview_raw=RAW;app.render_preview();await pilot.pause()
            self.assertFalse(app.query_one('#split-preview',Button).disabled)
            line=next(line.text for line in preview.lines if 'old value' in line.text)
            self.assertIn('│',line)
            self.assertIn('new value',line)

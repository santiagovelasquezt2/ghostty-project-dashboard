import os
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess
from textual import events
from textual.widgets import TextArea, Static
from dashboard.notes import Notes, NotesApp, BulletEditor

class NotesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,DASHBOARD_STATE_DIR=self.temp.name)
        self.env.start(); self.addCleanup(self.env.stop)

    async def test_edit_delete_empty_note_and_undo(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(60,20)) as pilot:
            editor=app.query_one(TextArea)
            await pilot.press('a','b','enter','c','backspace','backspace')
            self.assertEqual(editor.text,'ab')
            await pilot.press('ctrl+z')
            self.assertIn('\n',editor.text)
            await pilot.press('ctrl+shift+z')
            self.assertEqual(editor.text,'ab')
            app.query_one(Notes).save()
            editor.select_all()
            await pilot.press('backspace')
            self.assertEqual(editor.text,'')
            app.query_one(Notes).save()
            self.assertEqual(app.query_one(Notes).path.read_text(),'')

    async def test_mac_shortcuts_and_terminal_forwarded_equivalents(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(60,20)) as pilot:
            editor=app.query_one(TextArea)
            await pilot.press('a','b','c')
            await pilot.press('super+z'); self.assertEqual(editor.text,'')
            await pilot.press('super+shift+z'); self.assertEqual(editor.text,'abc')
            editor.load_text('first second third');editor.move_cursor((0,0))
            await pilot.press('shift+right');self.assertEqual(editor.selected_text,'f')
            editor.move_cursor((0,0))
            await pilot.press('super+alt+shift+right');self.assertEqual(editor.selected_text,'first')
            await pilot.press('super+a');self.assertEqual(editor.selected_text,editor.text)
            editor.move_cursor((0,0));await pilot.press('f7');self.assertEqual(editor.selected_text,editor.text)
            editor.move_cursor((0,0));await pilot.press('ctrl+shift+right');self.assertEqual(editor.selected_text,'first')

    async def test_command_shift_selects_line_and_document_boundaries(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(24,18)) as pilot:
            editor=app.query_one(TextArea)
            text='above\n    a long middle line that wraps in the narrow notes panel\nbelow'
            editor.load_text(text)
            for key,expected in (
                ('super+shift+left','    a long'),
                ('super+shift+right',' middle line that wraps in the narrow notes panel'),
                ('super+shift+up','above\n    a long'),
                ('super+shift+down',' middle line that wraps in the narrow notes panel\nbelow'),
                ('shift+home','    a long'),
                ('shift+end',' middle line that wraps in the narrow notes panel'),
                ('ctrl+shift+home','above\n    a long'),
                ('ctrl+shift+end',' middle line that wraps in the narrow notes panel\nbelow'),
            ):
                editor.move_cursor((1,10))
                await pilot.press(key)
                self.assertEqual(editor.selected_text,expected,key)
                self.assertEqual(editor.text,text)
            editor.move_cursor((1,10))
            await pilot.press('super+shift+up','super+shift+down')
            self.assertEqual(editor.selected_text,' middle line that wraps in the narrow notes panel\nbelow')
            editor.load_text('');await pilot.press('super+shift+down')
            self.assertEqual(editor.selected_text,'')

    async def test_footer_labels_are_rendered_not_hidden_by_button_borders(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(45,59)) as pilot:
            for width in (45,22,80):
                await pilot.resize_terminal(width,59);await pilot.pause()
                for name, label in (('notes-copy','Copy'),('notes-paste','Paste'),('notes-delete','Delete')):
                    button=app.query_one('#'+name)
                    rendered=''.join(button.render_line(y).text for y in range(button.size.height))
                    self.assertIn(label,rendered)
                    self.assertLessEqual(button.region.bottom,app.size.height)

    async def test_delete_button_first_middle_last_and_restore(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(50,20)) as pilot:
            editor=app.query_one(TextArea)
            for row,expected in [(0,'two\nthree'),(1,'one\nthree'),(2,'one\ntwo')]:
                editor.load_text('one\ntwo\nthree');editor.move_cursor((row,0))
                await pilot.click('#notes-delete')
                self.assertEqual(editor.text,expected)
                await pilot.press('ctrl+z')
                self.assertEqual(editor.text,'one\ntwo\nthree')
            editor.load_text('only')
            await pilot.click('#notes-delete')
            self.assertEqual(editor.text,'')

    async def test_paste_selection_copy_and_save_reload(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(60,20)) as pilot:
            editor=app.query_one(TextArea)
            await pilot.press('a','b','enter','c')
            editor.selection=type(editor.selection)((0,0),(0,2))
            with patch('dashboard.notes.subprocess.run') as copied:
                await pilot.click('#notes-copy'); await pilot.pause()
                self.assertEqual(copied.call_args.kwargs['input'],'ab')
            # Reversed selection must replace exactly the selected text.
            editor.selection=type(editor.selection)((1,1),(0,0))
            with patch('dashboard.notes.subprocess.run',return_value=CompletedProcess([],0,stdout='• first\r\n- second\nthird')):
                await pilot.click('#notes-paste'); await pilot.pause()
            self.assertEqual(editor.text,'first\nsecond\nthird')
            app.query_one(Notes).save()
            self.assertEqual(app.query_one(Notes).path.read_text(),'• first\n• second\n• third')
            self.assertEqual(Notes('/tmp/notes-test').initial,editor.text)
            editor.move_cursor((2,5))
            with patch('dashboard.notes.subprocess.run') as copied:
                await pilot.click('#notes-copy'); await pilot.pause()
                self.assertEqual(copied.call_args.kwargs['input'],'• first\n• second\n• third')

    async def test_native_paste_has_no_duplicate_bullets(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(40,15)) as pilot:
            editor=app.query_one(TextArea)
            editor.post_message(events.Paste('• one\n• two'))
            await pilot.pause()
            self.assertEqual(editor.text,'one\ntwo')

    async def test_resize_wrap_and_gutter_do_not_change_text_or_selection(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(50,20)) as pilot:
            editor=app.query_one(BulletEditor)
            editor.load_text('a long note with enough words to wrap over multiple screen rows\nsecond')
            editor.selection=type(editor.selection)((0,2),(0,9))
            original,selection=editor.text,editor.selection
            await pilot.resize_terminal(22,12); await pilot.pause()
            self.assertEqual(editor.text,original)
            self.assertEqual(editor.selection,selection)
            self.assertEqual(editor.render_line(0).text[:3],' • ')
            self.assertEqual(editor.render_line(1).text[:3],'   ')
            for selector in ('#notes-copy','#notes-paste','#notes-delete','#notes-narrow','#notes-widen'):
                self.assertTrue(app.query_one(selector).region.width>0)
                self.assertLessEqual(app.query_one(selector).region.right,app.size.width)
            with patch('dashboard.notes.resize_notes') as resize:
                await pilot.click('#notes-widen');await app.workers.wait_for_complete()
                resize.assert_called_once_with(64)

    async def test_external_change_is_not_overwritten(self):
        app=NotesApp('/tmp/notes-test')
        async with app.run_test(size=(50,20)) as pilot:
            notes=app.query_one(Notes);editor=app.query_one(TextArea)
            editor.load_text('original');notes.save()
            notes.path.write_text('• changed elsewhere')
            editor.load_text('unsaved here');notes.save()
            self.assertEqual(notes.path.read_text(),'• changed elsewhere')
            self.assertFalse(notes.save_enabled)
            self.assertIn('changed elsewhere',str(notes.query_one('#notes-status',Static).render()))

class ResizeOwnershipTests(unittest.TestCase):
    def test_resize_targets_invoking_notes_pane(self):
        from dashboard.notes import resize_notes
        from dashboard import cli, native, native_workspace
        with patch.dict(os.environ,TMUX_PANE='%7'), patch.object(cli,'tmux',return_value='$3') as tmux, patch.object(native_workspace,'controller',return_value='$1'), patch.object(cli,'option',side_effect=lambda s,k:'%7' if k=='notes' else 'native'), patch.object(cli,'locked'), patch.object(native_workspace,'read_state',return_value={'terminals':{'top':'notes-id'}}), patch.object(native,'resize') as resize:
            resize_notes(-64)
            tmux.assert_called_once_with('display-message','-p','-t','%7','#{session_id}')
            resize.assert_called_once_with({'terminals':{'top':'notes-id'}},'top','up',64)

    def test_resize_rejects_other_pane(self):
        from dashboard.notes import resize_notes
        with patch.dict(os.environ,TMUX_PANE='%8'), patch('dashboard.cli.tmux',return_value='$1'), patch('dashboard.native_workspace.controller',return_value='$1'), patch('dashboard.cli.option',return_value='%7'), patch('dashboard.native.resize') as resize:
            with self.assertRaises(RuntimeError): resize_notes(64)
            resize.assert_not_called()

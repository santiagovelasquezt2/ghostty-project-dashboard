import unittest
from unittest.mock import patch
from dashboard import geometry, native
from test_native import STATE

class GeometryTests(unittest.TestCase):
    def test_measurements_use_cell_pixels_and_matching_view(self):
        with patch('dashboard.geometry.cli.option',side_effect=lambda s,k:k), patch('dashboard.geometry.cli.tmux',side_effect=lambda *a,**k:'other|900|10\n'+a[2]+'|40|16'):
            self.assertEqual(geometry.measurements('session')['terminal'],(640,16))

    def test_restore_resizes_first_border_and_stops_when_close(self):
        state={**STATE,'saved_geometry':{'left,terminal,monitor':{'left':600,'terminal':900,'monitor':500}}}
        with patch('dashboard.geometry.measurements',side_effect=[{'left':(1000,10),'terminal':(500,10),'monitor':(500,10)}, {'left':(600,10),'terminal':(900,10),'monitor':(500,10)}]), patch('dashboard.geometry.time.sleep'), patch.object(native,'resize') as resize:
            geometry.restore('session',state)
        resize.assert_called_once_with(state,'left','left',400)

    def test_leave_preflight_includes_notes_and_resize_is_bounded(self):
        state={**STATE,'terminals':{**STATE['terminals'],'notes':'notes-id'}}
        self.assertIn('"notes-id"',native.build_leave_script(state,'/tmp/project'))
        with self.assertRaises(ValueError): native.resize(state,'notes','left',-5)
        with patch.object(native,'_execute',return_value='DASHBOARD_RESIZED') as execute:
            native.resize(state,'notes','right',100)
        self.assertIn('resize_split:right,100',execute.call_args.args[0])
        self.assertIn('dashboardIds does not contain "notes-id"',execute.call_args.args[0])

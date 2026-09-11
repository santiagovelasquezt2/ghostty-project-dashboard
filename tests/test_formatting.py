import subprocess
import tempfile
import unittest
from pathlib import Path
from dashboard.formatting import formatted_diff

class FormattingTests(unittest.TestCase):
    def test_formats_versions_without_writing_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def git(*args):
                return subprocess.check_output(['git','-C',directory,*args],stderr=subprocess.DEVNULL).decode().strip()
            git('init','-b','main')
            cases={'data.json': ('{"one":1,"nested":{"two":2}}','{"one":2,"nested":{"two":2}}'),
                   'style.css': ('body{color:red;margin:0}', 'body{color:blue;margin:0}'),
                   'app.tsx': ('export const App=()=>{return <div>Hello</div>}', 'export const App=()=>{return <div>World</div>}'),
                   'app.jsx': ('export const App=()=>{return <div>Hello</div>}', 'export const App=()=>{return <div>World</div>}')}
            for path,(before,after) in cases.items(): (root/path).write_text(before)
            git('add','.')
            git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','base')
            base=git('rev-parse','HEAD')
            for path,(before,after) in cases.items(): (root/path).write_text(after)
            (root/'.prettierrc.cjs').write_text("throw new Error('Project config must not execute');")
            status=git('status','--porcelain')
            for path,(_,after) in cases.items():
                result=formatted_diff(directory,base,path)
                self.assertIn('@@',result)
                self.assertGreater(len(result.splitlines()),5)
                self.assertEqual((root/path).read_text(),after)
            self.assertEqual(status,git('status','--porcelain'))
            (root/'data.json').write_text('{invalid')
            with self.assertRaises(ValueError): formatted_diff(directory,base,'data.json')
            self.assertIn('Formatting-only changes',formatted_diff(directory,base,'data.json',committed=True,head=base))

"""Format full before/after contents in memory, then compare for preview only."""
import difflib
import json
import os
from pathlib import Path
import subprocess
from . import gitdata

PARSERS = {'.json': 'json-stringify', '.jsonc': 'jsonc', '.css': 'css', '.scss': 'scss',
           '.html': 'html', '.htm': 'html', '.js': 'babel', '.jsx': 'babel',
           '.mjs': 'babel', '.cjs': 'babel', '.ts': 'typescript', '.tsx': 'typescript'}
LIMIT = 512 * 1024

def revision_text(root, revision, path):
    entry = gitdata._git(root, 'ls-tree', '-z', revision, '--', path)
    if not entry:
        return ''
    if not entry.startswith(b'100'):
        raise ValueError('Non-regular file')
    data = gitdata._git(root, 'show', f'{revision}:{path}', limit=LIMIT)
    if b'\0' in data:
        raise ValueError('Binary file')
    return data.decode('utf-8')

def formatted_diff(root, base, path, *, committed=False, head='HEAD', old_path=None):
    parser = PARSERS.get(Path(path).suffix.lower())
    if parser is None:
        raise ValueError('Unsupported file type')
    before = revision_text(root, base, old_path or path)
    if committed:
        after = revision_text(root, head, path)
    elif not os.path.lexists(Path(root) / path):
        after = ''
    else:
        source = Path(root) / path
        if source.is_symlink() or not source.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('Linked file')
        data, binary = gitdata._untracked_data(root, path, [LIMIT], preview=True)
        if binary or len(data) > LIMIT:
            raise ValueError('File too large or binary')
        after = data.decode('utf-8')
    candidates = [Path(__file__).resolve().parent.parent / 'formatter',
                  Path.home() / '.local/share/ghostty-project-dashboard/app/formatter']
    directory = next((p for p in candidates if (p / 'node_modules/prettier').is_dir()), candidates[0])
    result = subprocess.run(['node', str(directory / 'format.mjs')],
        input=json.dumps(dict(before=before, after=after, parser=parser)),
        capture_output=True, text=True, timeout=8, cwd=directory)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError('Formatter unavailable or invalid syntax')
    before, after = json.loads(result.stdout)
    diff = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile='a/' + gitdata.display_path(old_path or path), tofile='b/' + gitdata.display_path(path), n=5))
    if not diff:
        diff = 'Formatting-only changes · showing original diff so no changes are hidden.\n' + gitdata.file_diff(root, base, path, committed=committed, head=head, old_path=old_path)
    return gitdata.safe_text(diff)

"""Aligned, syntax-colored before/after previews; never modifies source files."""
import re
import json
from itertools import zip_longest
from rich.text import Text
from .highlighting import code_lines, highlight_diff
from .gitdata import safe_text

COLORS = ('#79d99b', '#ff7f8a', '#6fa7ff', '#808080', '#b6b6b6')
HUNK = re.compile(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')


def split_diff(raw, path=None, width=80):
    # At tiny widths retain readable columns and allow horizontal scrolling.
    width = max(60, width)
    lines = [s[:3000] for s in safe_text(raw).splitlines()[:10000]]
    # Size the columns to their full source lines, not the viewport. RichLog
    # measures this Text and provides horizontal scrolling without reflow.
    half = (width-3)//2
    old_width = max([half] + [Text(s[1:].expandtabs(4)).cell_len+8 for s in lines if s.startswith((' ', '-'))])
    result = Text(no_wrap=True, overflow='ignore')
    def row(left, right):
        left = left.copy(); right = right.copy()
        left.expand_tabs(4); right.expand_tabs(4)
        left.pad_right(max(0,old_width-left.cell_len))
        result.append(left); result.append(' │ ',style=COLORS[3]); result.append(right); result.append('\n')
    i, current = 0, path or ''
    while i < len(lines):
        line = lines[i]
        if line.startswith(('--- ', '+++ ')):
            name = line[4:].split('\t')[0]
            if name.startswith('"'):
                try: name = json.loads(name)
                except ValueError: name = ''
            if name != '/dev/null': current = name[2:] if name.startswith(('a/', 'b/')) else name
        match = HUNK.match(line)
        if not match:
            result.append(line+'\n', style=COLORS[3]); i += 1
            continue
        result.append(highlight_diff([line], current, COLORS))
        old_number, new_number = map(int, match.groups())
        i += 1
        hunk = []
        while i < len(lines) and lines[i].startswith((' ', '+', '-', '\\')):
            hunk.append(lines[i]); i += 1
        old = code_lines([s[1:] for s in hunk if s.startswith((' ', '-'))], current)
        new = code_lines([s[1:] for s in hunk if s.startswith((' ', '+'))], current)
        row(Text('Before',style=COLORS[3]), Text('After',style=COLORS[3]))
        oi = ni = 0
        removed, added = [], []
        def flush():
            for left, right in zip_longest(removed, added):
                row(left or Text(''), right or Text(''))
            removed.clear(); added.clear()
        def cell(content, number, prefix, color, background=None):
            value = Text(f'{number:>4} {prefix} ', style=color, no_wrap=True, overflow='ignore')
            value.append(content)
            if background: value.stylize('on ' + background)
            return value
        for s in hunk:
            prefix = s[0]
            if prefix == '\\':
                flush(); row(Text(s, style=COLORS[3]), Text(s, style=COLORS[3])); continue
            left = old[oi] if old is not None and oi < len(old) else Text(s[1:])
            right = new[ni] if new is not None and ni < len(new) else Text(s[1:])
            if prefix == ' ':
                flush()
                row(cell(left,old_number,' ',COLORS[3]),cell(right,new_number,' ',COLORS[3]))
            elif prefix == '-': removed.append(cell(left,old_number,'−',COLORS[1],'#381b22'))
            else: added.append(cell(right,new_number,'+',COLORS[0],'#123022'))
            if prefix in (' ', '-'): oi += 1; old_number += 1
            if prefix in (' ', '+'): ni += 1; new_number += 1
        flush()
    if len(raw.splitlines()) > 10000: result.append('Preview truncated after 10,000 lines.',style=COLORS[3])
    return result

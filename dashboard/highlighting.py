"""Literal diff highlighting using the VSCode Modern Black syntax palette."""
import json
import re
from pathlib import PurePosixPath
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.style import Style
from pygments.token import Token
from rich.text import Text

class ModernBlack(Style):
    background_color = '#000000'
    styles = {
        Token: '#cccccc', Token.Comment: '#6a9955', Token.Keyword: '#c586c0',
        Token.Keyword.Constant: '#569cd6', Token.Keyword.Type: '#4ec9b0',
        Token.Name: '#9cdcfe', Token.Name.Function: '#dcdcaa',
        Token.Name.Class: '#4ec9b0', Token.Name.Namespace: '#4ec9b0',
        Token.Name.Attribute: '#91cbea', Token.Name.Tag: '#569cd6',
        Token.Literal.String: '#ba846e', Token.Literal.String.Escape: '#c9ae76',
        Token.Literal.Number: '#569cd6', Token.Operator: '#cccccc',
        Token.Punctuation: '#cccccc', Token.Error: '#cccccc',
    }

LANGUAGES = {'.json': 'json', '.jsonc': 'json', '.ts': 'typescript', '.tsx': 'tsx', '.js': 'javascript', '.jsx': 'jsx',
             '.mjs': 'javascript', '.cjs': 'javascript', '.html': 'html', '.htm': 'html', '.css': 'css'}

def code_lines(lines: list[str], path: str) -> list[Text] | None:
    language = LANGUAGES.get(PurePosixPath(path).suffix.lower())
    if not language:
        return None
    text = Text()
    for token, value in lex('\n'.join(lines), get_lexer_by_name(language, stripnl=False, ensurenl=False)):
        color = ModernBlack.style_for_token(token)['color'] or 'cccccc'
        text.append(value, style='#' + color)
    return text.split('\n', allow_blank=True)

def highlight_diff(lines: list[str], path: str | None, colors: tuple[str, str, str, str, str]) -> Text:
    green, red, blue, muted, neutral = colors
    result = Text(no_wrap=True, overflow='ignore')
    current = path or ''
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('diff --git'):
            current = path or ''
        if line.startswith(('--- ', '+++ ')):
            candidate = line[4:].split('\t')[0]
            if candidate.startswith('"'):
                try: candidate = json.loads(candidate)
                except ValueError: candidate = ''
            if candidate != '/dev/null':
                current = candidate[2:] if candidate.startswith(('a/', 'b/')) else candidate
        if line.startswith('@@'):
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)", line)
            if match:
                old, old_count, new, new_count, context = match.groups()
                def label(start, count):
                    count = int(count) if count is not None else 1
                    start = int(start)
                    return "none" if count == 0 else str(start) if count == 1 else f"{start}–{start + count - 1}"
                text = f"Before: lines {label(old, old_count)} → After: lines {label(new, new_count)}"
                result.append(text + (" · " + context.strip() if context.strip() else "") + "\n", blue)
            else:
                result.append(line + '\n', blue)
            i += 1
            hunk = []
            while i < len(lines) and (lines[i].startswith((' ', '+', '-', '\\'))):
                hunk.append(lines[i]); i += 1
            old = code_lines([s[1:] for s in hunk if s.startswith((' ', '-'))], current)
            new = code_lines([s[1:] for s in hunk if s.startswith((' ', '+'))], current)
            oi = ni = 0
            for s in hunk:
                prefix = s[:1]
                color = green if prefix == '+' else red if prefix == '-' else neutral
                source = old if prefix == '-' else new
                index = oi if prefix == '-' else ni
                line_start = len(result)
                if prefix != '\\' and source is not None and index < len(source):
                    result.append(prefix, style='bold ' + color)
                    result.append(source[index])
                    result.append('\n')
                else:
                    result.append(s + '\n', color)
                if prefix in ('+', '-'):
                    result.stylize('on #123022' if prefix == '+' else 'on #381b22', line_start, len(result)-1)
                oi += prefix in (' ', '-')
                ni += prefix in (' ', '+')
            continue
        style = muted if line.startswith(('diff --git', 'index ', '--- ', '+++ ')) else green if line.startswith('+') else red if line.startswith('-') else neutral
        result.append(line + '\n', style)
        i += 1
    return result

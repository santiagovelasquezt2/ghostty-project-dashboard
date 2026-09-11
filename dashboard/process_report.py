"""On-demand, read-only process context for a user-reviewed clipboard report."""
from datetime import datetime
import re
from .processdata import Process, _run
from .gitdata import safe_text


def redact(text: str) -> str:
    text = re.sub(r'(?i)(\bBearer\s+)[^\s"\x27]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:--?|\b)(?:api[-_]?key|access[-_]?token|token|password|passwd|secret|authorization)(?:=|\s+))(?:("[^"]*"|\x27[^\x27]*\x27)|\S+)', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    return safe_text(text)


def collect_report(item: Process) -> str:
    pid = str(item.pid)
    def ps(field):
        return _run('/bin/ps', '-ww', '-p', pid, '-o', field + '=')
    started = ps('lstart')
    if not started or not started.strip():
        return f'Process {item.pid} ({safe_text(item.name)}) has exited or is no longer accessible. No current details are available.'
    executable = (ps('comm') or '').strip()
    ppid = (ps('ppid') or '').strip()
    lines = ['PROCESS INVESTIGATION REPORT', 'Captured: ' + datetime.now().astimezone().strftime('%Y-%m-%d %I:%M:%S %p %Z'),
             f'Name: {item.name}', f'PID: {pid}', 'Started: ' + started.strip(),
             'Executable: ' + (executable or 'Unavailable'), 'Parent PID: ' + (ppid or 'Unavailable')]
    if '.app/' in executable:
        lines.append('Application bundle: ' + executable.split('.app/')[0] + '.app')
    if ppid.isdecimal():
        lines.append('Parent executable: ' + (_run('/bin/ps', '-p', ppid, '-o', 'comm=') or 'Unavailable').strip())
    for label, field in [('Owner', 'user'), ('State', 'state'), ('Elapsed time', 'etime'), ('Total CPU time', 'time'), ('Command line', 'command')]:
        lines.append(label + ': ' + (ps(field) or 'Unavailable').strip())
    lines += ['', 'Resource snapshot from dashboard:', f'CPU: {item.cpu:.2f}% (100% = one core)',
              f'Resident memory: {item.memory_bytes:,} bytes',
              'GPU: ' + (f'{item.gpu:.2f}% execution time / elapsed time' if item.gpu is not None else 'Unavailable or sampling'),
              'Reported GPU time: ' + (f'{item.gpu_time_ns:,} ns (current contexts, not lifetime)' if item.gpu_time_ns is not None else 'Unavailable')]
    for title, options in [('Working directory', ['-d', 'cwd']), ('Open files (first 80 lines)', []), ('Network connections', ['-i'])]:
        output = _run('/usr/sbin/lsof', '-nP', '-a', '-p', pid, *options)
        lines += ['', title + ':', '\n'.join(output.splitlines()[:80]) if output else 'None reported or access unavailable.']
    if ps('lstart') != started or (ps('comm') or '').strip() != executable:
        return f'Process {pid} exited or changed while reading. Reopen its details for a fresh report.'
    lines += ['', 'Interpretation: this is a point-in-time observation. State, resource use, open files, and sockets are evidence, not proof of purpose or safety.',
              'For the agent: explain what this process likely belongs to, what the evidence shows it is doing, and what remains uncertain. Do not stop or modify it without asking.',
              'Environment variables and file contents were not collected. Common credential arguments are redacted; review paths, arguments, and network addresses before sharing.']
    return redact('\n'.join(lines))[:30000]

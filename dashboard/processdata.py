"""Unprivileged macOS process and system sampling, with no process arguments.

Process CPU uses cumulative CPU-time differences between calls. 100% means one
fully occupied logical CPU, matching Activity Monitor's process convention.
System CPU is the busy fraction across all CPUs. Process memory is resident
bytes (RSS), which can include shared pages and must not be summed as system
memory. System memory is physical RAM less free, inactive and speculative pages.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from functools import lru_cache
import math
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata


GPU_NOTE = "GPU counters have not been sampled yet."
_MAX_OUTPUT = 4 * 1024 * 1024
_LOCK = threading.Lock()
_PREVIOUS_PROCESSES: dict[tuple[int, str], float] = {}
_PREVIOUS_TIME: float | None = None
_PREVIOUS_CPU_TICKS: tuple[int, ...] | None = None


@dataclass
class Process:
    pid: int
    name: str
    cpu: float
    memory_bytes: int
    gpu: float | None = None
    gpu_time_ns: int | None = None


@dataclass
class Snapshot:
    processes: list[Process]
    cpu_percent: float | None
    memory_used: int | None
    memory_total: int | None
    gpu_available: bool = False
    gpu_note: str = GPU_NOTE
    gpu_sampling: bool = False


def _run(*args: str) -> str | None:
    env = os.environ.copy()
    env.update(LC_ALL="C", LANG="C")
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=3, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > _MAX_OUTPUT:
        return None
    return result.stdout.decode("utf-8", "replace")


def _short_name(command: str) -> str:
    # ps 'comm' is the executable path, never argv. Remove the directory before
    # returning it and replace controls/bidi formatting with inert characters.
    name = os.path.basename(command.strip()) or "unknown"
    return "".join("?" if unicodedata.category(char).startswith("C") else char
                   for char in name)[:160]


def _cpu_seconds(value: str) -> float:
    """Parse ps CPU times: MM:SS.xx, HH:MM:SS.xx, or DD-HH:MM:SS.xx."""
    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        days = int(day_text)
    parts = value.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Invalid CPU time")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    seconds += days * 86400
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid CPU time")
    return seconds


def _process_rows(output: str) -> list[tuple[int, str, float, int, str]]:
    rows = []
    for line in output.splitlines():
        # lstart has five fields in the fixed C locale. The final field retains
        # spaces in executable paths, such as Google Chrome Helper (Renderer).
        fields = line.strip().split(None, 8)
        if len(fields) != 9:
            continue
        try:
            pid = int(fields[0])
            seconds = _cpu_seconds(fields[1])
            memory = int(fields[2]) * 1024
            if pid < 0 or memory < 0:
                continue
        except ValueError:
            continue  # A process can disappear while ps builds its output.
        identity = " ".join(fields[3:8])
        rows.append((pid, identity, seconds, memory, _short_name(fields[8])))
    return rows


def _sample_processes(rows: list[tuple[int, str, float, int, str]],
                      now: float) -> list[Process]:
    global _PREVIOUS_PROCESSES, _PREVIOUS_TIME
    elapsed = now - _PREVIOUS_TIME if _PREVIOUS_TIME is not None else 0.0
    maximum_cpu = max(1, os.cpu_count() or 1) * 100.0
    current: dict[tuple[int, str], float] = {}
    processes = []
    for pid, start, seconds, memory, name in rows:
        key = (pid, start)
        previous = _PREVIOUS_PROCESSES.get(key)
        # A newly observed process has no prior sample; do not substitute a
        # lifetime-average percentage or attribute a reused PID's CPU to it.
        cpu = 0.0
        if previous is not None and elapsed > 0 and seconds >= previous:
            cpu = min(maximum_cpu, max(0.0, (seconds - previous) / elapsed * 100.0))
        processes.append(Process(pid, name, cpu, memory))
        current[key] = seconds
    _PREVIOUS_PROCESSES = current
    _PREVIOUS_TIME = now
    return processes


@lru_cache(maxsize=1)
def _mach_api():
    if sys.platform != "darwin":
        return None
    try:
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        library.mach_host_self.argtypes = []
        library.mach_host_self.restype = ctypes.c_uint32
        library.host_statistics.argtypes = [ctypes.c_uint32, ctypes.c_int,
                                            ctypes.POINTER(ctypes.c_int32),
                                            ctypes.POINTER(ctypes.c_uint32)]
        library.host_statistics.restype = ctypes.c_int
        # Hold one host port for the lifetime of this collector, avoiding a new
        # send right per refresh. HOST_CPU_LOAD_INFO is available unprivileged.
        return library, library.mach_host_self()
    except (OSError, AttributeError):
        return None


def _cpu_ticks() -> tuple[int, ...] | None:
    api = _mach_api()
    if api is None:
        return None
    library, host = api
    counts = (ctypes.c_int32 * 4)()
    length = ctypes.c_uint32(4)
    if library.host_statistics(host, 3, counts, ctypes.byref(length)) != 0 or length.value != 4:
        return None
    # ABI order is user, system, idle, nice; the exported counters are uint32.
    return tuple(value & 0xFFFFFFFF for value in counts)


def _system_cpu(ticks: tuple[int, ...] | None) -> float | None:
    global _PREVIOUS_CPU_TICKS
    previous = _PREVIOUS_CPU_TICKS
    _PREVIOUS_CPU_TICKS = ticks
    if ticks is None or previous is None:
        return None
    deltas = [(new - old) & 0xFFFFFFFF for old, new in zip(previous, ticks)]
    total = sum(deltas)
    if total == 0:
        return None
    return min(100.0, max(0.0, (total - deltas[2]) / total * 100.0))


@lru_cache(maxsize=1)
def _memory_total() -> int | None:
    raw = _run("/usr/sbin/sysctl", "-n", "hw.memsize")
    try:
        total = int(raw.strip()) if raw is not None else 0
        return total if total > 0 else None
    except ValueError:
        return None


def _memory_used(raw: str | None, total: int | None) -> int | None:
    if raw is None or total is None:
        return None
    page_match = re.search(r"page size of (\d+) bytes", raw)
    if not page_match:
        return None
    pages: dict[str, int] = {}
    for line in raw.splitlines()[1:]:
        match = re.match(r"([^:]+):\s+(\d+)\.?$", line.strip())
        if match:
            pages[match[1]] = int(match[2])
    needed = ("Pages free", "Pages inactive", "Pages speculative")
    if any(key not in pages for key in needed):
        return None
    available = sum(pages[key] for key in needed) * int(page_match.group(1))
    return min(total, max(0, total - available))


def collect() -> Snapshot:
    """Return a fresh read-only sample; call about every two seconds.

    The first sample has no system CPU rate and zero process CPU rates. Sorting
    by CPU becomes meaningful after the next sample. Processes that exit are
    simply absent. A failed process enumeration raises a plain error for the UI;
    unavailable system counters return None rather than invented values.
    """
    with _LOCK:
        raw = _run("/bin/ps", "-ww", "-axo", "pid=,time=,rss=,lstart=,comm=")
        if raw is None:
            raise RuntimeError("macOS process information is temporarily unavailable.")
        now = time.monotonic()
        rows = _process_rows(raw)
        processes = _sample_processes(rows, now)
        from . import gpudata
        gpu = gpudata.sample({pid: start for pid, start, *_ in rows})
        for process in processes:
            process.gpu = gpu.rates.get(process.pid)
            process.gpu_time_ns = gpu.cumulative_ns.get(process.pid)
        system_cpu = _system_cpu(_cpu_ticks())
        total = _memory_total()
        used = _memory_used(_run("/usr/bin/vm_stat"), total)
        return Snapshot(processes, system_cpu, used, total,
                        gpu_available=gpu.available, gpu_note=gpu.note, gpu_sampling=gpu.sampling)

"""Unprivileged per-process GPU time from the Apple Silicon graphics driver.

AGXDeviceUserClient exposes AppUsage.accumulatedGPUTime in nanoseconds. We
difference each registry client's counter before combining by PID, so a closed
client cannot subtract GPU work from surviving clients. PID creation identities
protect against PID reuse. New or reset clients establish a fresh baseline.
Changes in AppUsage entry count or API labels also reset the client baseline.
The driver exposes no context IDs, so replacements with the same array shape
cannot be distinguished reliably from continued work.

GPU percentage is execution nanoseconds / wall-clock nanoseconds * 100. It is
not divided by GPU core count or clamped to 100: overlapping GPU queues can
report execution time that differs from whole-device busy percentage. Context
turnover can make an interval incomplete; known positive work is still useful.
These driver properties are readable without root but are not a stable public
macOS API, so unavailable/malformed data degrades to explicit unavailable state.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import plistlib
import re
import subprocess
import tempfile
import threading
import time
from xml.parsers.expat import ExpatError


MAX_OUTPUT_BYTES = 4 * 1024 * 1024
READ_TIMEOUT = 3
_UNAVAILABLE = "GPU activity is unavailable from this Mac's driver."
_LOCK = threading.Lock()
ClientKey = tuple[int, str, int, tuple[str | None, ...]]
_PREVIOUS: dict[ClientKey, int] = {}
_PREVIOUS_TIME: int | None = None


@dataclass
class Snapshot:
    rates: dict[int, float | None]
    cumulative_ns: dict[int, int]
    available: bool
    sampling: bool = False
    note: str = ""


class GPUUnavailable(RuntimeError):
    pass


def _read() -> tuple[int, list[dict]]:
    """Read only AGX clients; never dump registry data or process arguments."""
    started = time.monotonic_ns()
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                ["/usr/sbin/ioreg", "-r", "-c", "AGXDeviceUserClient", "-a"],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                timeout=READ_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GPUUnavailable(_UNAVAILABLE) from exc
        sampled = (started + time.monotonic_ns()) // 2
        if result.returncode != 0:
            raise GPUUnavailable(_UNAVAILABLE)
        output.seek(0)
        data = output.read(MAX_OUTPUT_BYTES + 1)
    if len(data) > MAX_OUTPUT_BYTES:
        raise GPUUnavailable("GPU driver results exceeded the monitor's read limit.")
    try:
        nodes = plistlib.loads(data)
    except (plistlib.InvalidFileException, ExpatError, ValueError, TypeError, OverflowError) as exc:
        raise GPUUnavailable(_UNAVAILABLE) from exc
    if not isinstance(nodes, list):
        raise GPUUnavailable(_UNAVAILABLE)
    return sampled, nodes


def _clients(nodes: list[dict], identities: dict[int, str]) -> tuple[bool, dict[ClientKey, int]]:
    supported = False
    counters: dict[ClientKey, int] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        usage = node.get("AppUsage")
        if not isinstance(usage, list):
            continue
        values = [entry["accumulatedGPUTime"] for entry in usage
                  if isinstance(entry, dict) and type(entry.get("accumulatedGPUTime")) is int
                  and entry["accumulatedGPUTime"] >= 0]
        if len(values) != len(usage):
            continue  # Malformed counters do not establish driver support.
        creator = node.get("IOUserClientCreator", "")
        match = re.match(r"^pid\s+(\d+)(?:,|$)", creator) if isinstance(creator, str) else None
        entry_id = node.get("IORegistryEntryID")
        if match is None or type(entry_id) is not int or entry_id < 0:
            continue
        # Both counters and structural identities must be usable. Validate
        # before filtering identities because healthy clients can belong to
        # a process that exited since the ps sample.
        supported = True
        pid = int(match.group(1))
        identity = identities.get(pid)
        if identity is None:
            continue  # Process exited, or was absent from the process sample.
        # A context can disappear without its registry client disappearing.
        # A changed shape therefore needs a new baseline even if its sum grew.
        shape = tuple(entry.get("API") if isinstance(entry.get("API"), str) else None
                      for entry in usage)
        counters[(pid, identity, entry_id, shape)] = sum(values)
    return supported, counters


def sample(process_identities: dict[int, str]) -> Snapshot:
    """Sample GPU counters for known PIDs and their process creation identities.

    First observations have None rates and sampling=True. If a PID gains a new
    client while existing clients report work, its known positive rate remains
    visible; sampling=True signals that new client baselines are incomplete.
    Zero is reported only for a fully observed stable set of current clients.
    Cumulative totals cover currently existing clients, not destroyed contexts.
    """
    global _PREVIOUS, _PREVIOUS_TIME
    with _LOCK:
        try:
            timestamp, nodes = _read()
            supported, counters = _clients(nodes, process_identities)
        except GPUUnavailable as exc:
            _PREVIOUS, _PREVIOUS_TIME = {}, None
            return Snapshot({}, {}, False, note=str(exc))
        if not supported:
            _PREVIOUS, _PREVIOUS_TIME = {}, None
            malformed = any(isinstance(node, dict) and "AppUsage" in node for node in nodes)
            note = "GPU driver counters have an unsupported format." if malformed else _UNAVAILABLE
            return Snapshot({}, {}, False, note=note)

        elapsed = timestamp - _PREVIOUS_TIME if _PREVIOUS_TIME is not None else 0
        cumulative: dict[int, int] = defaultdict(int)
        changes: dict[int, int] = defaultdict(int)
        established: set[int] = set()
        baselining: set[int] = set()
        current_processes = {key[:2] for key in counters}
        for key in _PREVIOUS.keys() - counters.keys():
            if key[:2] in current_processes:
                # A vanished client may have done final work after the last
                # sample. A surviving client's zero is not proof of PID idle.
                baselining.add(key[0])
        for key, current in counters.items():
            pid = key[0]
            cumulative[pid] += current
            previous = _PREVIOUS.get(key)
            if elapsed <= 0 or previous is None or current < previous:
                baselining.add(pid)
                continue
            established.add(pid)
            changes[pid] += current - previous

        rates: dict[int, float | None] = {}
        for pid in cumulative:
            if pid in established and (pid not in baselining or changes[pid] > 0):
                rates[pid] = changes[pid] / elapsed * 100.0
            else:
                rates[pid] = None
        _PREVIOUS, _PREVIOUS_TIME = counters, timestamp
        sampling = bool(baselining)
        return Snapshot(rates, dict(cumulative), True, sampling,
                        "Sampling GPU activity…" if sampling else "")

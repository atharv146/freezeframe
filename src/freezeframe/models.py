"""Plain data types shared by the recorder, the store and the diagnosis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Sample:
    """Whole-machine measurements at one moment."""

    ts: float  # wall-clock unix time
    interval: float  # seconds since the previous sample (monotonic clock)
    cpu: float  # percent of the whole machine, 0-100
    mem_total: int
    mem_available: int
    swap_used: int
    swap_in: float  # bytes per second
    swap_out: float
    disk_read: float
    disk_write: float
    net_rx: float
    net_tx: float
    pressure: int | None = None  # macOS: 1 normal, 2 warning, 4 critical
    thermal: int | None = None  # macOS: 0 nominal ... 4 sleeping
    load1: float | None = None
    battery: float | None = None
    plugged: bool | None = None
    self_cpu: float = 0.0  # Freezeframe's own CPU use, percent of one core

    @property
    def mem_available_pct(self) -> float:
        return 100.0 * self.mem_available / self.mem_total if self.mem_total else 100.0


@dataclass
class AppUsage:
    """One app's processes added together at one moment."""

    app: str
    cpu: float  # percent of one core (200 = two full cores)
    rss: int  # bytes of memory
    nproc: int = 1
    top_name: str = ""
    read_bps: float | None = None
    write_bps: float | None = None


@dataclass
class Event:
    ts: float
    kind: str
    title: str
    severity: int = 1  # 0 info, 1 notable, 2 problem, 3 serious
    end_ts: float | None = None
    app: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    source: str | None = None  # unique key for events imported from OS reports
    id: int | None = None


# Things a person experiences as "something went wrong".
INCIDENT_KINDS = {"stall", "hang", "crash", "panic", "mark", "hardware", "shutdown"}

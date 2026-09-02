"""Bounded display timing evidence and single-threaded deadline pacing.

Flip return is a host-side observation, never a panel/scanout timestamp.
"""

from collections import deque
import json
import math
from pathlib import Path
import statistics
import time

import pygame


DISPLAY_JOURNAL_NAME = "display_timestamps.jsonl"
DISPLAY_FORMAT = "segcom-display-v2"


class FramePacer:
    def __init__(self, anchor_ns: int, refresh_hz: float = 60.0):
        if not math.isfinite(refresh_hz) or not 1 <= refresh_hz <= 1000:
            raise ValueError("Refresh rate must be between 1 and 1000 Hz")
        self.nominal_period_ns = round(1_000_000_000 / refresh_hz)
        self.period_ns = self.nominal_period_ns
        self.deadline_ns = anchor_ns + self.period_ns
        self.render_budget_ns = min(1_500_000, self.period_ns // 3)
        self._last_flip_ns = None
        self._render_times = deque(maxlen=120)
        self._intervals = deque(maxlen=120)

    def skip_expired(self, now_ns: int) -> int:
        """Keep deadlines on the existing time grid, never run catch-up bursts."""
        if now_ns < self.deadline_ns:
            return 0
        skipped = (now_ns - self.deadline_ns) // self.period_ns + 1
        self.deadline_ns += skipped * self.period_ns
        return skipped

    def wait(self, exit_requested) -> tuple[bool, int]:
        skipped = self.skip_expired(time.monotonic_ns())
        while True:
            if exit_requested():
                return False, skipped
            remaining = self.deadline_ns - self.render_budget_ns - time.monotonic_ns()
            if remaining <= 0:
                # The OS may have overslept a whole refresh; do not submit stale work.
                extra = self.skip_expired(time.monotonic_ns())
                skipped += extra
                if not extra:
                    return True, skipped
                continue
            if remaining > 1_000_000:
                pygame.time.wait(min(10, max(1, (remaining - 1_000_000) // 1_000_000)))
            else:
                # Only the final sub-millisecond tail spins. Clock.tick(60) is
                # not phase locked to vsync and must not add a second limiter.
                target = self.deadline_ns - self.render_budget_ns
                while time.monotonic_ns() < target:
                    pass

    def observe(self, marker_ns, submit_ns, flip_return_ns, skipped_periods=0) -> dict:
        period = self.period_ns
        interval = None if self._last_flip_ns is None else flip_return_ns - self._last_flip_ns
        irregular = interval is not None and not 0.75 * period <= interval <= 1.25 * period
        missed = max(0, (flip_return_ns - self.deadline_ns + period // 4) // period)
        result = {
            "marker_ns": marker_ns,
            "deadline_ns": self.deadline_ns,
            "submit_ns": submit_ns,
            "flip_return_ns": flip_return_ns,
            "frame_period_ns": period,
            "interval_ns": interval,
            "render_budget_ns": self.render_budget_ns,
            "late_submit": submit_ns > self.deadline_ns,
            "skipped_periods": skipped_periods + missed,
            "irregular_interval": irregular,
        }
        self._render_times.append(max(0, submit_ns - marker_ns))
        ordered = sorted(self._render_times)
        p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
        self.render_budget_ns = min(period // 2, max(500_000, p95 + 750_000))

        # Learn cadence only from plausible, blocking flips; a 33 ms missed
        # refresh must not silently redefine a requested 60 Hz display as 30 Hz.
        blocking = flip_return_ns - submit_ns >= 200_000
        phase_error = flip_return_ns - result["deadline_ns"]
        near_deadline = abs(phase_error) <= min(250_000, period // 20)
        phase_candidate = blocking and near_deadline and not result["late_submit"]
        if interval is not None and not irregular and phase_candidate and not missed and not skipped_periods:
            self._intervals.append(interval)
            if len(self._intervals) >= 8:
                candidate = round(statistics.median(self._intervals))
                if 0.9 * self.nominal_period_ns <= candidate <= 1.1 * self.nominal_period_ns:
                    self.period_ns = candidate
        self.deadline_ns += (missed + 1) * self.period_ns
        if phase_candidate and not missed and not irregular:
            # Small phase correction to the flip-return proxy, not a reset to
            # now + period (which would accumulate work/sleep delays).
            limit = self.period_ns // 20
            self.deadline_ns += max(-limit, min(limit, phase_error))
        self._last_flip_ns = flip_return_ns
        return result


class DisplayJournal:
    """Write timing outside the sample/draw/flip critical section."""

    def __init__(self, path: str | Path | None, metadata: dict):
        self._file = None if path is None else Path(path).open("x", encoding="utf-8", buffering=65536)
        self._last_flush_ns = time.monotonic_ns()
        self._last_warning_ns = 0
        self.count = 0
        self.skipped = 0
        self.irregular = 0
        self.late = 0
        self._write({"kind": "session", "format": DISPLAY_FORMAT, **metadata})
        if self._file:
            self._file.flush()

    def _write(self, value):
        if self._file:
            self._file.write(json.dumps(value, separators=(",", ":")) + "\n")

    def append(self, corner: int, timing: dict):
        self._write({"kind": "frame", "index": self.count, "corner": corner, **timing})
        self.count += 1
        self.skipped += timing["skipped_periods"]
        self.irregular += bool(timing["irregular_interval"])
        self.late += bool(timing["late_submit"])
        now = time.monotonic_ns()
        if self._file and now - self._last_flush_ns >= 1_000_000_000:
            self._file.flush()
            self._last_flush_ns = now
        if (timing["skipped_periods"] or timing["irregular_interval"] or timing["late_submit"]):
            if now - self._last_warning_ns >= 1_000_000_000:
                print(f"[CALIBRATION] Display timing: {self.skipped} missed-period candidate(s), "
                      f"{self.irregular} irregular interval(s), {self.late} late submission(s)", flush=True)
                self._last_warning_ns = now

    def pause(self, paused: bool, timestamp_ns: int):
        """Record a held marker without pretending it is a new presentation."""
        self._write({"kind": "pause", "paused": paused, "monotonic_ns": timestamp_ns,
                     "last_frame_index": self.count - 1})
        if self._file:
            self._file.flush()

    def close(self):
        try:
            self._write({"kind": "summary", "frames": self.count,
                         "missed_period_candidates": self.skipped,
                         "irregular_intervals": self.irregular, "late_submissions": self.late})
        finally:
            if self._file:
                self._file.close()
        print(f"[CALIBRATION] Presented {self.count} markers; {self.skipped} missed-period "
              f"candidate(s), {self.irregular} irregular interval(s), {self.late} late submission(s). "
              "Flip timing is not a physical scanout measurement.", flush=True)

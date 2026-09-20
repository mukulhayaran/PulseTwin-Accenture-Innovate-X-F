"""The shift clock, in one place.

`_shift_window` was defined inside `serve/main.py` and used only by the SSE alert budget.
`serve/routers/intelligence.py` needs the IDENTICAL boundaries -- a shift report whose
window disagrees with the alert budget's window by even an hour would report a different
number of alerts than the supervisor's meter showed, and the two would be impossible to
reconcile on stage.

It lives here rather than being imported from `main` because `main` imports the routers:
the other direction is a cycle. Stdlib only, so the import wall is untouched.
"""

from __future__ import annotations

from datetime import datetime

SHIFT_HOURS = 8
SHIFTS_PER_DAY = 24 // SHIFT_HOURS


def shift_window(ts: str) -> tuple[str, str]:
    """The 8-hour shift boundaries containing `ts`, as ISO-8601 strings.

    Shifts are anchored at 00:00 / 08:00 / 16:00. The end is 07:59:59 past the start
    rather than the next start, so two adjacent windows cannot both claim one instant --
    the alert budget counts distinct units per window and a shared boundary would
    double-count every unit that landed exactly on it.
    """
    dt = datetime.fromisoformat(ts)
    start_h = (dt.hour // SHIFT_HOURS) * SHIFT_HOURS
    start = dt.replace(hour=start_h, minute=0, second=0, microsecond=0)
    end = start.replace(hour=start_h + SHIFT_HOURS - 1, minute=59, second=59)
    return start.isoformat(), end.isoformat()


def shift_label(ts: str) -> str:
    """A stable, human-readable name for the shift containing `ts`.

    Derived from the clock, never from `units.shift_id` -- that column is the SIMULATOR's
    crew label and pairing the two would imply a crew-to-window mapping the artifact does
    not actually guarantee. Shift labels are about time; crews are about people, and
    CLAUDE.md §3 keeps people out of this surface entirely.
    """
    dt = datetime.fromisoformat(ts)
    return f"{dt.date().isoformat()} S{(dt.hour // SHIFT_HOURS) + 1}"


__all__ = ["SHIFT_HOURS", "SHIFTS_PER_DAY", "shift_window", "shift_label"]

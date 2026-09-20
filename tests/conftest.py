"""Suite-wide test fixtures.

The repository deliberately keeps each test file self-contained -- every module repeats
its own skip guard and builds its own client. That convention is kept. This file exists
for ONE thing that cannot be solved per-file, because the state it manages is shared by
every test in the process.

THE RATE LIMITER. `serve/middleware/rate_limit.py` keeps a sliding window per
`role:ip`. Under TestClient every test in the suite is the same client IP, and the whole
suite runs inside one process, so the counters accumulate across FILES. The leadership
limit is 180/min; the suite makes far more than that within a minute, and the tests that
happen to run after the budget is exhausted fail with a 429 whose body then gets indexed
as if it were the success payload (`KeyError: 'current_model'`).

Those tests pass in isolation and fail in the suite, which is the worst failure mode a
test can have: it looks like a real regression in whatever file happened to be running.

The fix is to reset the counters between tests rather than to raise or disable the limit.
The limiter is real production behaviour and stays exactly as strict as it is; what
changes is that each test starts from a clean window, which is what a single user hitting
the API actually experiences.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limit_window():
    """Give every test its own rate-limit window.

    Imported lazily and guarded: `tests/test_simulator.py` and friends never import
    `serve`, and a suite run on a machine without fastapi installed must still collect.
    """
    try:
        from serve.middleware import rate_limit
    except ImportError:
        yield
        return

    rate_limit.reset()
    yield
    rate_limit.reset()

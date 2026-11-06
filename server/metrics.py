import statistics
import time
from collections import defaultdict

STARTED = time.time()
_counters = defaultdict(float)
_timings = defaultdict(list)


def inc(name, value=1):
    _counters[name] += value


def observe(name, seconds):
    values = _timings[name]
    values.append(float(seconds))
    if len(values) > 500:
        del values[: len(values) - 500]


def snapshot():
    timings = {}
    for name, values in _timings.items():
        if not values:
            continue
        ordered = sorted(values)
        index = min(len(ordered) - 1, int(len(ordered) * 0.95))
        timings[name] = {
            "count": len(values),
            "avg": round(statistics.mean(values), 3),
            "p95": round(ordered[index], 3),
            "max": round(ordered[-1], 3),
        }
    return {
        "started_at": STARTED,
        "uptime_seconds": round(time.time() - STARTED, 1),
        "counters": {key: int(value) for key, value in _counters.items()},
        "timings": timings,
    }

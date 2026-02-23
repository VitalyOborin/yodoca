"""Human-friendly formatting utilities for agent tool output.

Thin facade over the `humanize` library, providing a stable internal API
that all extensions can import without coupling to third-party signatures.
"""

import time as _time
from datetime import datetime, timezone

import humanize

_LOCAL_TZ = datetime.now(timezone.utc).astimezone().tzinfo


def relative_time(epoch: int | None) -> str:
    """Convert Unix epoch to English relative string, e.g. '3 hours ago'.

    Returns empty string for None, 0, or non-positive values.
    """
    if not epoch or epoch <= 0:
        return ""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return humanize.naturaltime(dt)


def format_event_time(ts: int | None) -> dict[str, str]:
    """Derive human-readable timestamp fields from a Unix epoch integer.

    Returns a dict with:
      - event_time_iso:      RFC 3339 in UTC (e.g. "2026-02-23T15:23:47+00:00")
      - event_time_local:    local wall-clock (e.g. "2026-02-23 18:23:47 UTC+3")
      - event_time_tz:       timezone label (e.g. "UTC+3")
      - event_time_relative: relative time (e.g. "3 hours ago")

    All fields are empty strings when ts is None, 0, or non-positive.
    """
    empty = {
        "event_time_iso": "",
        "event_time_local": "",
        "event_time_tz": "",
        "event_time_relative": "",
    }
    if not ts or ts <= 0:
        return empty
    utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    local_dt = utc_dt.astimezone(_LOCAL_TZ)
    tz_name = local_dt.strftime("%Z")
    return {
        "event_time_iso": utc_dt.isoformat(),
        "event_time_local": local_dt.strftime(f"%Y-%m-%d %H:%M:%S {tz_name}"),
        "event_time_tz": tz_name,
        "event_time_relative": relative_time(ts),
    }


def format_bytes(n: int | float) -> str:
    """Format byte count as human-readable string, e.g. '1.5 MB'.

    Uses binary suffixes (KiB/MiB/GiB) via humanize for precision.
    """
    if n <= 0:
        return "0 Bytes"
    return humanize.naturalsize(n, binary=True)

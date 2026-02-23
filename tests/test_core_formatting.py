"""Tests for core.utils.formatting — shared humanization utilities."""

import time

from core.utils.formatting import format_bytes, format_event_time, relative_time


class TestRelativeTime:
    """relative_time() produces English human-readable strings."""

    def test_recent_past(self) -> None:
        ts = int(time.time()) - 30
        result = relative_time(ts)
        assert result
        assert "second" in result or "now" in result

    def test_hours_ago(self) -> None:
        ts = int(time.time()) - 7200
        result = relative_time(ts)
        assert "hour" in result

    def test_days_ago(self) -> None:
        ts = int(time.time()) - 3 * 86400
        result = relative_time(ts)
        assert "day" in result

    def test_none_returns_empty(self) -> None:
        assert relative_time(None) == ""

    def test_zero_returns_empty(self) -> None:
        assert relative_time(0) == ""

    def test_negative_returns_empty(self) -> None:
        assert relative_time(-100) == ""


class TestFormatEventTime:
    """format_event_time() returns complete dict with all fields."""

    def test_all_keys_present(self) -> None:
        ts = int(time.time()) - 600
        result = format_event_time(ts)
        assert set(result.keys()) == {
            "event_time_iso",
            "event_time_local",
            "event_time_tz",
            "event_time_relative",
        }

    def test_iso_is_utc(self) -> None:
        ts = int(time.time())
        result = format_event_time(ts)
        assert result["event_time_iso"].endswith("+00:00")

    def test_relative_is_nonempty(self) -> None:
        ts = int(time.time()) - 3600
        result = format_event_time(ts)
        assert result["event_time_relative"]

    def test_empty_for_none(self) -> None:
        result = format_event_time(None)
        assert all(v == "" for v in result.values())

    def test_empty_for_zero(self) -> None:
        result = format_event_time(0)
        assert all(v == "" for v in result.values())


class TestFormatBytes:
    """format_bytes() produces human-readable size strings."""

    def test_zero(self) -> None:
        assert format_bytes(0) == "0 Bytes"

    def test_kilobytes(self) -> None:
        result = format_bytes(1024)
        assert "Ki" in result or "K" in result

    def test_megabytes(self) -> None:
        result = format_bytes(10 * 1024 * 1024)
        assert "Mi" in result or "M" in result

    def test_gigabytes(self) -> None:
        result = format_bytes(2 * 1024 * 1024 * 1024)
        assert "Gi" in result or "G" in result

from datetime import datetime, timezone

from job_meta import classify_seniority, parse_posted, within_lookback


NOW = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)


def test_relative_minutes() -> None:
    iso, label = parse_posted("Coinbase · 52 minutes ago", now=NOW)
    assert label == "52 minutes ago"
    assert iso == "2026-11-08T17:08:00Z"


def test_relative_hours() -> None:
    iso, _ = parse_posted("Posted 3 hours ago", now=NOW)
    assert iso == "2026-11-08T15:00:00Z"


def test_lookback_keeps_unknown() -> None:
    assert within_lookback(None, 30, now=NOW) is True


def test_lookback_filters_old() -> None:
    iso, _ = parse_posted("5 hours ago", now=NOW)
    assert within_lookback(iso, 30, now=NOW) is False
    assert within_lookback(iso, 360, now=NOW) is True


def test_seniority() -> None:
    assert classify_seniority("Senior Software Engineer - Frontend") == "Senior"
    assert classify_seniority("Staff Software Engineer") == "Staff"
    assert classify_seniority("Software Architect") == "Architect"
    assert classify_seniority("Software Engineer") == "Mid"
    assert classify_seniority("New Grad Software Engineer") == "Entry"


if __name__ == "__main__":
    test_relative_minutes()
    test_relative_hours()
    test_lookback_keeps_unknown()
    test_lookback_filters_old()
    test_seniority()
    print("job_meta ok")

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Current time as a timezone-aware UTC datetime.

    All datetimes in the system are aware UTC; the DB columns are timestamptz.
    """
    return datetime.now(timezone.utc)

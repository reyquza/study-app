from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.config import APP_TIMEZONE


_app_zone = ZoneInfo(APP_TIMEZONE)


def local_today() -> date:
    """The learner's current date, independent of the server's timezone."""
    return datetime.now(_app_zone).date()


def local_day_start_utc(day: date) -> datetime:
    """Midnight of `day` in the app timezone, as an aware UTC datetime for DB comparisons."""
    return datetime.combine(day, time.min, tzinfo=_app_zone).astimezone(timezone.utc)

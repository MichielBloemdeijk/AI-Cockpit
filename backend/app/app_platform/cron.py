"""Five-field cron schedules for app functions, in the owner's time zone.

`minute hour day-of-month month day-of-week`, with `*`, lists, ranges, steps
and month/day names (`0 8 * * MON`, `*/15 9-17 * * 1-5`), plus `@hourly`,
`@daily`, `@weekly` and `@monthly`. As in standard cron, when both day fields
are restricted a day matches either of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.jarvis_time import DEFAULT_TIME_ZONE

ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}
MONTHS = {name: number for number, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
DAYS = {name: number for number, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}


class CronError(ValueError):
    pass


def _field(text: str, low: int, high: int, names: dict[str, int]) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text.isdigit() and int(step_text) > 0 else None
        if step_text and step is None:
            raise CronError(f"Invalid step in {part!r}")

        def number(token: str) -> int:
            token = token.upper()
            if token in names:
                return names[token]
            if not token.isdigit():
                raise CronError(f"Invalid value {token!r}")
            return int(token)

        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, last = base.split("-", 1)
            start, end = number(first), number(last)
        else:
            start = number(base)
            end = high if step else start
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise CronError(f"{part!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step or 1))
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class Schedule:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 is Sunday
    any_day: bool
    any_weekday: bool

    def _day_matches(self, moment: datetime) -> bool:
        day = moment.day in self.days
        weekday = (moment.isoweekday() % 7) in self.weekdays
        if self.any_day or self.any_weekday:
            return day and weekday
        return day or weekday

    def next_after(self, moment: datetime, time_zone: str = DEFAULT_TIME_ZONE) -> datetime:
        """The first matching minute strictly after `moment` (aware), as UTC."""
        zone = ZoneInfo(time_zone)
        local = moment.astimezone(zone).replace(second=0, microsecond=0, tzinfo=None) + timedelta(minutes=1)
        limit = local + timedelta(days=366 * 5)
        while local < limit:
            if local.month not in self.months:
                local = (local.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            elif not self._day_matches(local):
                local = local.replace(hour=0, minute=0) + timedelta(days=1)
            elif local.hour not in self.hours:
                local = local.replace(minute=0) + timedelta(hours=1)
            elif local.minute not in self.minutes:
                local += timedelta(minutes=1)
            else:
                return local.replace(tzinfo=zone).astimezone(timezone.utc)
        raise CronError("The schedule never fires")


def parse(expression: str) -> Schedule:
    text = ALIASES.get(expression.strip().lower(), expression.strip())
    fields = text.split()
    if len(fields) != 5:
        raise CronError("A schedule has five fields: minute hour day-of-month month day-of-week")
    minute, hour, day, month, weekday = fields
    weekdays = _field(weekday, 0, 7, DAYS)
    schedule = Schedule(
        minutes=_field(minute, 0, 59, {}), hours=_field(hour, 0, 23, {}), days=_field(day, 1, 31, {}),
        months=_field(month, 1, 12, MONTHS), weekdays=frozenset(value % 7 for value in weekdays),
        any_day=day == "*", any_weekday=weekday == "*")
    schedule.next_after(datetime.now(timezone.utc))  # rejects schedules that never fire, like Feb 30
    return schedule

"""Authoritative clock and local-time interpretation for Jarvis."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIME_ZONE = "Europe/Amsterdam"
_FIXED_OFFSET = re.compile(r"^(?:UTC|GMT)?(?P<sign>[+-])(?P<hours>\d{1,2}):(?P<minutes>\d{2})$", re.I)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ClientTemporalContext:
    time_zone: str
    utc_offset_minutes: int | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ResolvedReminderTime:
    due_at: datetime
    timing: dict[str, Any]
    time_zone: str


def _time_zone(value: str) -> tuple[str, tzinfo]:
    requested = value.strip()
    if not requested:
        raise ValueError("Time zone is required")
    try:
        zone = ZoneInfo(requested)
        return zone.key, zone
    except ZoneInfoNotFoundError:
        match = _FIXED_OFFSET.fullmatch(requested)
        if match is None:
            raise ValueError(f"Unknown time zone: {requested}") from None
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes"))
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            raise ValueError(f"Invalid UTC offset: {requested}")
        total = (hours * 60 + minutes) * (1 if match.group("sign") == "+" else -1)
        label = f"UTC{'+' if total >= 0 else '-'}{abs(total) // 60:02d}:{abs(total) % 60:02d}"
        return label, timezone(timedelta(minutes=total), name=label)


def canonical_time_zone(value: str) -> str:
    return _time_zone(value)[0]


def normalize_client_context(
    context: Any | None,
    *,
    fallback_time_zone: str = DEFAULT_TIME_ZONE,
) -> ClientTemporalContext:
    fallback = canonical_time_zone(fallback_time_zone or DEFAULT_TIME_ZONE)
    if context is None:
        return ClientTemporalContext(fallback, None, None)
    values = context.model_dump() if hasattr(context, "model_dump") else dict(context)
    try:
        zone = canonical_time_zone(str(values.get("time_zone") or ""))
    except ValueError:
        zone = fallback
    observed = values.get("observed_at")
    if isinstance(observed, str):
        observed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    if isinstance(observed, datetime):
        if observed.tzinfo is None:
            raise ValueError("Client clock observation must include a UTC offset")
        observed = observed.astimezone(timezone.utc)
    else:
        observed = None
    offset = values.get("utc_offset_minutes")
    return ClientTemporalContext(zone, None if offset is None else int(offset), observed)


def temporal_frame(*, time_zone: str, now: datetime | None = None) -> dict[str, Any]:
    timestamp = (now or utc_now()).astimezone(timezone.utc)
    zone_name, zone = _time_zone(time_zone)
    local = timestamp.astimezone(zone)
    offset = local.utcoffset() or timedelta()
    return {
        "as_of_utc": timestamp.isoformat(),
        "local_time": local.isoformat(),
        "time_zone": zone_name,
        "utc_offset_minutes": int(offset.total_seconds() // 60),
    }


def resolve_reminder_time(
    *,
    timing: Any | None = None,
    due_at: datetime | str | None = None,
    active_time_zone: str = DEFAULT_TIME_ZONE,
    now: datetime | None = None,
) -> ResolvedReminderTime:
    timestamp = (now or utc_now()).astimezone(timezone.utc)
    active_name, active_zone = _time_zone(active_time_zone)
    if timing is None:
        if due_at is None:
            raise ValueError("Reminder timing is required")
        instant = _datetime(due_at)
        if instant.tzinfo is None:
            instant = _resolve_local(instant.date(), instant.time(), active_zone)
        resolved = instant.astimezone(timezone.utc)
        timing_payload = {"kind": "instant", "due_at": resolved.isoformat(), "legacy_due_at": True}
        zone_name = active_name
    else:
        values = timing.model_dump(mode="json") if hasattr(timing, "model_dump") else dict(timing)
        kind = str(values.get("kind") or "")
        if kind == "after":
            seconds = int(values.get("seconds") or 0)
            if seconds < 1 or seconds > 31_536_000:
                raise ValueError("Reminder delay must be between one second and one year")
            resolved = timestamp + timedelta(seconds=seconds)
            timing_payload = {"kind": "after", "seconds": seconds}
            zone_name = active_name
        elif kind == "instant":
            instant = _datetime(values.get("due_at"))
            if instant.tzinfo is None:
                raise ValueError("An instant reminder time must include a UTC offset")
            resolved = instant.astimezone(timezone.utc)
            timing_payload = {"kind": "instant", "due_at": resolved.isoformat()}
            zone_name = active_name
        elif kind == "local":
            zone_name, zone = _time_zone(str(values.get("time_zone") or active_name))
            local_time = _clock_time(values.get("time"))
            local_date = _calendar_date(values.get("date"))
            if local_date is None:
                current_local = timestamp.astimezone(zone)
                local_date = current_local.date()
                candidate = _resolve_local(local_date, local_time, zone)
                if candidate.astimezone(timezone.utc) <= timestamp:
                    local_date += timedelta(days=1)
            resolved = _resolve_local(local_date, local_time, zone).astimezone(timezone.utc)
            timing_payload = {
                "kind": "local",
                "date": local_date.isoformat(),
                "time": local_time.isoformat(),
                "time_zone": zone_name,
            }
        else:
            raise ValueError("Reminder timing kind must be after, local, or instant")
    if resolved <= timestamp:
        raise ValueError("Reminder time must be in the future")
    return ResolvedReminderTime(resolved, timing_payload, zone_name)


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Use an unambiguous ISO-8601 reminder time") from exc


def _calendar_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("Use an ISO-8601 local reminder date") from exc


def _clock_time(value: Any) -> time:
    if isinstance(value, time):
        parsed = value
    else:
        try:
            parsed = time.fromisoformat(str(value or ""))
        except ValueError as exc:
            raise ValueError("Use an unambiguous local reminder time") from exc
    if parsed.tzinfo is not None:
        raise ValueError("A local reminder time must not include a UTC offset")
    return parsed


def _resolve_local(day: date, clock: time, zone: tzinfo) -> datetime:
    naive = datetime.combine(day, clock.replace(tzinfo=None))
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        utc_value = aware.astimezone(timezone.utc)
        if utc_value.astimezone(zone).replace(tzinfo=None) == naive:
            candidates[utc_value] = aware
    if not candidates:
        raise ValueError("That local time does not exist because the clocks change. Which time should I use?")
    if len(candidates) > 1:
        raise ValueError("That local time occurs twice because the clocks change. Which one should I use?")
    return next(iter(candidates.values()))

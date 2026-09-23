from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def end_time(start: time, duration_minutes: int) -> time:
    result = datetime.combine(date(2000, 1, 1), start) + timedelta(minutes=duration_minutes)
    if result.date() != date(2000, 1, 1):
        raise ValueError("Service cannot cross midnight")
    return result.time()


def available_slots(
    day: date,
    duration_minutes: int,
    work_start: time,
    work_end: time,
    weekdays: frozenset[int],
    step_minutes: int,
    occupied: list[tuple[time, time]],
    now: datetime,
) -> list[time]:
    if day.weekday() not in weekdays:
        return []
    cursor = datetime.combine(day, work_start)
    close = datetime.combine(day, work_end)
    result: list[time] = []
    while cursor + timedelta(minutes=duration_minutes) <= close:
        finish = cursor + timedelta(minutes=duration_minutes)
        if cursor > now.replace(tzinfo=None) and all(
            finish.time() <= start or cursor.time() >= end for start, end in occupied
        ):
            result.append(cursor.time())
        cursor += timedelta(minutes=step_minutes)
    return result


def local_now(timezone_name: str) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))

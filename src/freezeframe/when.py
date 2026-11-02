"""Understanding times the way people say them.

"3:42pm", "around 3", "20 minutes ago", "yesterday evening", "between 2 and
3pm", "the last 10 minutes". Each becomes a window of time: an exact minute
gets a few minutes either side, a vague "this morning" gets the whole morning.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

UNITS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "d": 86400, "day": 86400}
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
         "thirty": 30, "forty": 40, "forty-five": 45, "few": 3, "couple of": 2, "a couple of": 2, "a few": 3, "several": 5}
PARTS_OF_DAY = {"morning": (6, 12), "afternoon": (12, 17), "evening": (17, 22), "night": (20, 28), "tonight": (17, 28)}


@dataclass
class Window:
    start: float
    end: float
    label: str  # how to say it back, e.g. "around 3:00 PM"

    @property
    def center(self) -> float:
        return (self.start + self.end) / 2


class NotUnderstood(ValueError):
    pass


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _day_word(d: datetime, now: datetime) -> str:
    """' yesterday', ' on Saturday', or nothing for today."""
    if d.date() == now.date():
        return ""
    if d.date() == (now - timedelta(days=1)).date():
        return " yesterday"
    return d.strftime(" on %A")


def _amount(text: str) -> float | None:
    text = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    return float(WORDS[text]) if text in WORDS else None


def _time_of_day(text: str) -> tuple[int, int, bool] | None:
    """(hour, minute, exact) from '3', '3pm', '3:42', '15:42', 'noon'. exact means minutes were given."""
    text = text.strip().replace(".", "")
    if text in ("noon", "midday"):
        return 12, 0, False
    if text == "midnight":
        return 0, 0, False
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?", text)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if hour > 23 or minute > 59:
        return None
    if ampm and ampm.startswith("p") and hour < 12:
        hour += 12
    elif ampm and ampm.startswith("a") and hour == 12:
        hour = 0
    elif not ampm and 1 <= hour <= 12:
        hour = -hour  # ambiguous: resolved to the most recent matching time
    return hour, minute, m.group(2) is not None


def _resolve(hour: int, minute: int, day: datetime | None, now: datetime) -> datetime:
    """The most recent past moment matching hour:minute (on `day` if given)."""
    candidates = [abs(hour) % 24, (abs(hour) + 12) % 24] if hour < 0 else [hour]
    best = None
    for base in ([day] if day else [now, now - timedelta(days=1)]):
        for h in candidates:
            t = base.replace(hour=h, minute=minute, second=0, microsecond=0)
            if t <= now + timedelta(minutes=1) and (best is None or t > best):
                best = t
    if best is None:  # a time later today on an explicit day: just use it
        best = (day or now).replace(hour=candidates[0], minute=minute, second=0, microsecond=0)
    return best


def parse(text: str, now: float | None = None) -> Window:
    """Turns a phrase into a window of time. Raises NotUnderstood."""
    now_ts = time.time() if now is None else now
    now_dt = datetime.fromtimestamp(now_ts)
    raw = text
    text = " ".join(text.strip().lower().replace("’", "'").split())
    text = re.sub(r"^(what happened|what was going on|why did it freeze|why was it slow)\s*", "", text)
    text = re.sub(r"\?$", "", text).strip()
    approx = bool(re.match(r"^(around|about|roughly|approximately|approx|~|sometime around|maybe)\b", text))
    text = re.sub(r"^(at|around|about|roughly|approximately|approx|~|sometime around|maybe|on)\s*", "", text).strip()
    if not text:
        raise NotUnderstood(raw)

    if text in ("now", "just now", "right now", "a moment ago", "a second ago", "earlier", "recently"):
        return Window(now_ts - 300, now_ts, "the last 5 minutes")

    # "the last 10 minutes", "past hour", "last 2 hours"
    m = re.fullmatch(r"(?:in )?(?:the )?(?:last|past) ?(.*?) ?(second|sec|minute|min|hour|hr|day)s?", text)
    if m:
        n = _amount(m.group(1) or "1")
        if n is not None:
            span = n * UNITS[m.group(2)]
            return Window(now_ts - span, now_ts, f"the last {m.group(1) + ' ' if m.group(1) else ''}{m.group(2)}{'s' if n != 1 else ''}")

    # "20 minutes ago", "an hour ago", "half an hour ago", "10m", "2h"
    m = re.fullmatch(r"(half an hour|half hour)(?: ago)?", text)
    if m:
        return Window(now_ts - 1800 - 300, now_ts - 1800 + 300, "about half an hour ago")
    m = re.fullmatch(r"(.+?) ?(s|sec|second|m|min|minute|h|hr|hour|d|day)s?(?: ago)?", text)
    if m and (text.endswith("ago") or re.fullmatch(r"\d+(\.\d+)? ?[smhd]", text) or m.group(1) in WORDS):
        n = _amount(m.group(1))
        if n is not None:
            ago = n * UNITS[m.group(2)]
            half = max(120.0, min(ago * 0.15, 1800.0))
            t = now_ts - ago
            return Window(t - half, min(now_ts, t + half), f"about {_clock(datetime.fromtimestamp(t))} ({text if 'ago' in text else text + ' ago'})")

    # "between 2 and 3pm", "from 2:30 to 3", "2-3pm"
    m = re.fullmatch(r"(?:between |from )?(.+?) ?(?:and|to|-|–|until) ?(.+)", text)
    if m:
        day, rest_a = _day_prefix(m.group(1), now_dt)
        a, b = _time_of_day(rest_a), _time_of_day(m.group(2))
        if a and b:
            if a[0] < 0 and b[0] >= 12 and abs(a[0]) < 12:
                a = (abs(a[0]) + 12, a[1], a[2])  # "2-3pm" means 2pm
            end = _resolve(b[0], b[1], day, now_dt)
            hours = [a[0]] if a[0] >= 0 else [abs(a[0]) % 24, (abs(a[0]) + 12) % 24]
            starts = [end.replace(hour=h, minute=a[1]) for h in hours]
            starts = [t for t in starts if t < end] or [t - timedelta(days=1) for t in starts]
            start = max(starts)
            return Window(start.timestamp(), end.timestamp(), f"{_clock(start)} to {_clock(end)}{_day_word(end, now_dt)}")

    # "yesterday evening", "this morning", "last night", "tonight"
    m = re.fullmatch(r"(today|yesterday|this|last)? ?(morning|afternoon|evening|night|tonight)", text)
    if m:
        which, part = m.group(1), m.group(2)
        h0, h1 = PARTS_OF_DAY[part]
        base = now_dt - timedelta(days=1) if which in ("yesterday",) or (which == "last" and part == "night") else now_dt
        if which == "last" and part == "night" and now_dt.hour >= 20:
            base = now_dt
        start = base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=h0)
        end = start + timedelta(hours=h1 - h0)
        shifted = start > now_dt
        if shifted:
            start, end = start - timedelta(days=1), end - timedelta(days=1)
        end = min(end, now_dt)
        word = {"night": "last night", "tonight": "last night" if shifted else "tonight"}.get(
            part, f"yesterday {part}" if shifted else f"{which or 'this'} {part}".replace("today", "this"))
        return Window(start.timestamp(), end.timestamp(), word)

    if text in ("today", "yesterday"):
        base = now_dt if text == "today" else now_dt - timedelta(days=1)
        start = base.replace(hour=0, minute=0, second=0, microsecond=0)
        return Window(start.timestamp(), min(now_ts, (start + timedelta(days=1)).timestamp()), text)

    # "3pm", "3:42", "yesterday at 4", "noon"
    day, rest = _day_prefix(text, now_dt)
    rest = re.sub(r"^(at|around|about) ", "", rest)
    tod = _time_of_day(rest)
    if tod:
        hour, minute, exact = tod
        t = _resolve(hour, minute, day, now_dt)
        half = (5 if approx else 3) * 60 if exact else 15 * 60
        start, end = t.timestamp() - half, min(now_ts, t.timestamp() + half)
        return Window(start, max(end, start + 60), f"{'around ' if approx or not exact else ''}{_clock(t)}{_day_word(t, now_dt)}")

    try:
        t = datetime.fromisoformat(text.upper() if "t" in text else text)
    except ValueError:
        raise NotUnderstood(raw) from None
    return Window(t.timestamp() - 180, t.timestamp() + 180, _clock(t) + _day_word(t, now_dt))


def _day_prefix(text: str, now: datetime) -> tuple[datetime | None, str]:
    m = re.match(r"^(today|yesterday|last night)\b,? ?(?:at )?(.*)$", text)
    if not m:
        return None, text
    base = now if m.group(1) == "today" else now - timedelta(days=1)
    return base, m.group(2)

"""India Standard Time as a fixed offset (no daylight saving), so Windows needs no timezone database."""

from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")

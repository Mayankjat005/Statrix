# This file is a part of Statrix
# Coding : Priyanshu Dey [@irisXDR]

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC datetime — replaces deprecated ``datetime.utcnow()``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

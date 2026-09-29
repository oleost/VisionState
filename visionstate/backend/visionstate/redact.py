"""Removes credentials from URLs and messages before they are logged, shown or exported."""

from __future__ import annotations

import re

from .settings import SECRET_QUERY_KEYS

MASK = "***"
_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")
_QUERY_SECRET = re.compile(
    r"(?P<key>[?&](?:" + "|".join(re.escape(k) for k in SECRET_QUERY_KEYS) + r"))=[^&#\s'\"]*",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """``rtsp://user:pw@cam/…`` → ``rtsp://***@cam/…``; ``?user=a&password=b`` → ``?user=***&password=***``."""
    text = _USERINFO.sub(lambda m: f"{m.group('scheme')}{MASK}@", text)
    return _QUERY_SECRET.sub(lambda m: f"{m.group('key')}={MASK}", text)


def has_credentials(url: str) -> bool:
    return redact(url) != url

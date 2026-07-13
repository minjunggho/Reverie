"""Minimal structured-ish logging setup."""
from __future__ import annotations

import logging

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def redact_database_url(value: str) -> str:
    """Return a useful database identity without credentials or query secrets.

    ``URL.render_as_string(hide_password=True)`` still includes usernames and
    arbitrary query parameters, so rebuild the URL from only its non-secret
    routing fields.  Never echo malformed input in the fallback.
    """
    try:
        parsed = make_url(value)
        return URL.create(
            drivername=parsed.drivername,
            host=parsed.host,
            port=parsed.port,
            database=parsed.database,
        ).render_as_string(hide_password=True)
    except (ArgumentError, TypeError, ValueError):
        return "<invalid-database-url>"

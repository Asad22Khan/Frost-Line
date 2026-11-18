"""Database connection factory for the legacy telemetry system (serverless PostgreSQL on Neon)."""

import time
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from src import config

# Neon suspends idle compute; the first connection after a pause needs a moment to wake it up.
MAX_ATTEMPTS = 4

ROLE_SETTINGS = {
    "admin": "DATABASE_URL",  # owner role, used only by setup scripts
    "agent": "AGENT_DATABASE_URL",  # read-only role used by the AI agent
    "audit": "AUDIT_DATABASE_URL",  # append-only audit log role used by application code
}


def _sqlalchemy_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache(maxsize=None)
def get_engine(role: str = "agent") -> Engine:
    """Return a pooled engine. role='admin' for setup scripts, role='agent' for the read-only AI user."""
    url = config.require(ROLE_SETTINGS[role])
    return create_engine(
        _sqlalchemy_url(url),
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 15},
    )


def wait_until_ready(engine: Engine) -> None:
    """Ping the database, retrying while a suspended serverless compute wakes up."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            wait = 3 * attempt
            print(f"Database not ready (attempt {attempt}/{MAX_ATTEMPTS}): {exc.orig}. Retrying in {wait}s...")
            time.sleep(wait)

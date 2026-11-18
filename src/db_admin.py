"""Owner-only helpers used by the setup scripts to create login roles and apply SQL files.

Never imported by the app: the deployed app has no owner credentials.
"""

import re
import secrets
from pathlib import Path

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

from src import config
from src.db import _sqlalchemy_url

ENV_FILE = config.PROJECT_ROOT / ".env"


def role_password(env_var: str) -> str:
    """Reuse the password already stored in `env_var` if there is one, otherwise generate a new one."""
    existing = config.env(env_var)
    if existing:
        return make_url(existing).password or secrets.token_urlsafe(24)
    return secrets.token_urlsafe(24)


def ensure_role(owner_engine: Engine, role: str, password: str) -> None:
    with owner_engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar()
        verb = "ALTER" if exists else "CREATE"
        stmt = sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
        conn.exec_driver_sql(stmt.as_string(conn.connection.driver_connection))
    print(f"  role {role}: {'updated' if exists else 'created'}")


def apply_sql_file(owner_engine: Engine, path: Path) -> None:
    with owner_engine.begin() as conn:
        # Execute through the raw psycopg connection without parameters, so a literal '%' in the
        # SQL (e.g. inside a COMMENT) isn't mistaken for a placeholder.
        conn.connection.driver_connection.execute(path.read_text(encoding="utf-8"))
    print(f"  applied {path.relative_to(config.PROJECT_ROOT)}")


def save_role_url(env_var: str, role: str, password: str) -> str:
    """Write `env_var=<connection string for role>` to .env, replacing any previous value."""
    owner_url = make_url(config.require("DATABASE_URL"))
    role_url = owner_url.set(username=role, password=password).render_as_string(hide_password=False)

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [ln for ln in lines if not re.match(rf"\s*{env_var}\s*=", ln)]
    lines.append(f"{env_var}={role_url}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  saved {env_var} to .env")
    return role_url


def run_checks(url: str, checks: list[tuple[str, str, bool]]) -> bool:
    """Run (description, SQL, should_succeed) checks as the given role. True if every outcome matches.
    Each check runs in a transaction that is rolled back, so a successful test INSERT leaves no row behind."""
    engine = create_engine(_sqlalchemy_url(url), connect_args={"connect_timeout": 15})
    all_ok = True
    for description, statement, should_succeed in checks:
        try:
            with engine.connect() as conn:
                result = conn.exec_driver_sql(statement)
                detail = f"returned {result.scalar()}" if result.returns_rows else "executed"
            succeeded = True
        except DBAPIError as exc:
            succeeded = False
            detail = str(exc.orig).splitlines()[0]
        ok = succeeded == should_succeed
        all_ok &= ok
        expectation = "allowed" if should_succeed else "blocked"
        print(f"  [{'PASS' if ok else 'FAIL'}] {description:<38} expected {expectation:<7} -> {detail}")
    engine.dispose()
    return all_ok

"""Phase 2: create the read-only AI agent role and the semantic view, then prove the lockdown works.

Steps
  1. Create (or update) the login role `fde_agent_ro` with a strong generated password.
  2. Apply sql/security_layer.sql as the owner (view, grants, role guard rails).
  3. Save AGENT_DATABASE_URL to .env so the password never appears in code or on screen.
  4. Connect AS the agent and run an attack checklist: every forbidden action must fail.

Usage:  python scripts/setup_security.py
"""

import re
import secrets
import sys
from pathlib import Path

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.db import _sqlalchemy_url, get_engine, wait_until_ready  # noqa: E402

AGENT_ROLE = "fde_agent_ro"
SQL_FILE = config.PROJECT_ROOT / "sql" / "security_layer.sql"
ENV_FILE = config.PROJECT_ROOT / ".env"


def agent_password() -> str:
    """Reuse the existing agent password if one is configured, otherwise generate a new one."""
    existing = config.env("AGENT_DATABASE_URL")
    if existing:
        return make_url(existing).password or secrets.token_urlsafe(24)
    return secrets.token_urlsafe(24)


def ensure_role(owner_engine, password: str) -> None:
    with owner_engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": AGENT_ROLE}).scalar()
        verb = "ALTER" if exists else "CREATE"
        stmt = sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(AGENT_ROLE), sql.Literal(password)
        )
        raw = conn.connection.driver_connection
        conn.exec_driver_sql(stmt.as_string(raw))
    print(f"  role {AGENT_ROLE}: {'updated' if exists else 'created'}")


def apply_sql_file(owner_engine) -> None:
    with owner_engine.begin() as conn:
        # Execute through the raw psycopg connection without parameters, so a literal '%' in the
        # SQL (e.g. inside a COMMENT) isn't mistaken for a placeholder.
        conn.connection.driver_connection.execute(SQL_FILE.read_text(encoding="utf-8"))
    print(f"  applied {SQL_FILE.relative_to(config.PROJECT_ROOT)}")


def save_agent_url(password: str) -> str:
    owner_url = make_url(config.require("DATABASE_URL"))
    agent_url = owner_url.set(username=AGENT_ROLE, password=password).render_as_string(hide_password=False)

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [ln for ln in lines if not re.match(r"\s*AGENT_DATABASE_URL\s*=", ln)]
    lines.append(f"AGENT_DATABASE_URL={agent_url}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("  saved AGENT_DATABASE_URL to .env")
    return agent_url


# (description, SQL, should_succeed)
CHECKS = [
    ("read the semantic view", "SELECT COUNT(*) FROM vw_active_fleet", True),
    ("read the raw legacy table", "SELECT * FROM public.tbl_sc_fleet_hist_raw LIMIT 1", False),
    ("delete rows through the view", "DELETE FROM vw_active_fleet", False),
    ("update the raw table", "UPDATE public.tbl_sc_fleet_hist_raw SET iot_temp_val_c = 0", False),
    ("drop the raw table", "DROP TABLE public.tbl_sc_fleet_hist_raw", False),
    ("create a new table", "CREATE TABLE public.pwned (x int)", False),
    ("escape read-only mode", "SET TRANSACTION READ WRITE; DELETE FROM vw_active_fleet", False),
    ("run a query past the 15s timeout", "SELECT pg_sleep(16)", False),
]


def run_checks(agent_url: str) -> bool:
    engine = create_engine(_sqlalchemy_url(agent_url), connect_args={"connect_timeout": 15})
    all_ok = True
    for description, statement, should_succeed in CHECKS:
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
        print(f"  [{'PASS' if ok else 'FAIL'}] {description:<34} expected {expectation:<7} -> {detail}")
    engine.dispose()
    return all_ok


def main() -> None:
    owner = get_engine("admin")
    print("Connecting as owner ...")
    wait_until_ready(owner)

    password = agent_password()
    print("Configuring security layer ...")
    ensure_role(owner, password)
    apply_sql_file(owner)
    agent_url = save_agent_url(password)

    print(f"Attacking the database as {AGENT_ROLE} (every forbidden action must be blocked) ...")
    if run_checks(agent_url):
        print("Security layer verified: the agent can only read fde_views.vw_active_fleet.")
    else:
        sys.exit("Security verification FAILED - do not connect the agent until this is fixed.")


if __name__ == "__main__":
    main()

"""Phase 2: create the read-only AI agent role and the semantic view, then prove the lockdown works.

Steps
  1. Create (or update) the login role `fde_agent_ro` with a strong generated password.
  2. Apply sql/security_layer.sql as the owner (view, grants, role guard rails).
  3. Save AGENT_DATABASE_URL to .env so the password never appears in code or on screen.
  4. Connect AS the agent and run an attack checklist: every forbidden action must fail.

Usage:  python scripts/setup_security.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.db import get_engine, wait_until_ready  # noqa: E402
from src.db_admin import apply_sql_file, ensure_role, role_password, run_checks, save_role_url  # noqa: E402

AGENT_ROLE = "fde_agent_ro"
ENV_VAR = "AGENT_DATABASE_URL"
SQL_FILE = config.PROJECT_ROOT / "sql" / "security_layer.sql"


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
    ("read the audit log", "SELECT COUNT(*) FROM fde_audit.agent_audit_log", False),
]


def main() -> None:
    owner = get_engine("admin")
    print("Connecting as owner ...")
    wait_until_ready(owner)

    password = role_password(ENV_VAR)
    print("Configuring security layer ...")
    ensure_role(owner, AGENT_ROLE, password)
    apply_sql_file(owner, SQL_FILE)
    agent_url = save_role_url(ENV_VAR, AGENT_ROLE, password)

    print(f"Attacking the database as {AGENT_ROLE} (every forbidden action must be blocked) ...")
    if run_checks(agent_url, CHECKS):
        print("Security layer verified: the agent can only read fde_views.vw_active_fleet.")
    else:
        sys.exit("Security verification FAILED - do not connect the agent until this is fixed.")


if __name__ == "__main__":
    main()

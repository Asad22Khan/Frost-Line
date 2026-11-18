"""Phase 5: create the append-only audit log and its dedicated role, then prove the rules hold.

Steps
  1. Create (or update) the login role `fde_audit_app` with a strong generated password.
  2. Apply sql/audit_layer.sql as the owner (schema, table, grants, append-only triggers).
  3. Save AUDIT_DATABASE_URL to .env.
  4. Connect AS fde_audit_app and check it can insert and read logs, but not change or delete them,
     and cannot touch the telemetry data.

Run scripts/setup_security.py first (the agent role must exist).
Usage:  python scripts/setup_audit.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.db import get_engine, wait_until_ready  # noqa: E402
from src.db_admin import apply_sql_file, ensure_role, role_password, run_checks, save_role_url  # noqa: E402

AUDIT_ROLE = "fde_audit_app"
ENV_VAR = "AUDIT_DATABASE_URL"
SQL_FILE = config.PROJECT_ROOT / "sql" / "audit_layer.sql"

INSERT = ("INSERT INTO agent_audit_log (session_id, turn_id, event_type, content) "
          "VALUES ('setup-check', 'setup-check', 'question', 'permission test (rolled back)')")

# (description, SQL, should_succeed). Each check is rolled back, so nothing is left in the log.
CHECKS = [
    ("insert an audit row", INSERT, True),
    ("read the audit log", "SELECT COUNT(*) FROM agent_audit_log", True),
    ("update an audit row", "UPDATE agent_audit_log SET content = 'tampered'", False),
    ("delete audit rows", "DELETE FROM agent_audit_log", False),
    ("truncate the audit log", "TRUNCATE agent_audit_log", False),
    ("drop the audit log", "DROP TABLE agent_audit_log", False),
    ("read the telemetry view", "SELECT COUNT(*) FROM fde_views.vw_active_fleet", False),
    ("read the raw legacy table", "SELECT COUNT(*) FROM public.tbl_sc_fleet_hist_raw", False),
]


def main() -> None:
    owner = get_engine("admin")
    print("Connecting as owner ...")
    wait_until_ready(owner)

    password = role_password(ENV_VAR)
    print("Configuring audit layer ...")
    ensure_role(owner, AUDIT_ROLE, password)
    apply_sql_file(owner, SQL_FILE)
    audit_url = save_role_url(ENV_VAR, AUDIT_ROLE, password)

    print(f"Checking permissions as {AUDIT_ROLE} ...")
    if run_checks(audit_url, CHECKS):
        print("Audit layer verified: the log is append-only and isolated from the telemetry data.")
    else:
        sys.exit("Audit verification FAILED - fix before enabling the app.")


if __name__ == "__main__":
    main()

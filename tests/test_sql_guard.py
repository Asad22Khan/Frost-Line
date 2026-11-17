import pytest

from src.sql_guard import MAX_ROWS, UnsafeQueryError, validate_and_limit

ALLOWED = [
    "SELECT COUNT(*) FROM vw_active_fleet",
    "SELECT AVG(cargo_temp_c) FROM fde_views.vw_active_fleet WHERE risk_classification = 'High Risk'",
    "SELECT date_trunc('month', event_time) AS m, COUNT(*) FROM vw_active_fleet GROUP BY 1 ORDER BY 1",
    "WITH hot AS (SELECT * FROM vw_active_fleet WHERE cargo_temp_c > 4) SELECT COUNT(*) FROM hot",
    "SELECT latitude, longitude FROM vw_active_fleet "
    "WHERE latitude BETWEEN 33.5 AND 34.2 AND longitude BETWEEN -118.5 AND -117.8",
]

BLOCKED = [
    ("DELETE FROM vw_active_fleet", "SELECT"),
    ("DROP TABLE tbl_sc_fleet_hist_raw", "SELECT"),
    ("UPDATE vw_active_fleet SET cargo_temp_c = 0", "SELECT"),
    ("SELECT 1; DROP TABLE tbl_sc_fleet_hist_raw", "one SQL statement"),
    ("SELECT * FROM public.tbl_sc_fleet_hist_raw", "not available"),
    ("SELECT * FROM tbl_sc_fleet_hist_raw", "not available"),
    ("SELECT * FROM pg_catalog.pg_roles", "not available"),
    ("SELECT * FROM vw_active_fleet v JOIN pg_user u ON true", "not available"),
    ("SELECT pg_sleep(30)", "not allowed"),
    ("SELECT pg_read_file('/etc/passwd')", "not allowed"),
    ("SELECT set_config('default_transaction_read_only', 'off', false)", "not allowed"),
    ("SELECT * INTO stolen FROM vw_active_fleet", "read-only"),
    ("", "Empty"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_queries_pass(sql):
    assert validate_and_limit(sql).upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize(("sql", "reason"), BLOCKED)
def test_dangerous_queries_are_rejected(sql, reason):
    with pytest.raises(UnsafeQueryError, match=reason):
        validate_and_limit(sql)


def test_limit_is_added_when_missing():
    assert validate_and_limit("SELECT * FROM vw_active_fleet").endswith(f"LIMIT {MAX_ROWS}")


def test_excessive_limit_is_capped():
    assert validate_and_limit("SELECT * FROM vw_active_fleet LIMIT 100000").endswith(f"LIMIT {MAX_ROWS}")


def test_small_limit_is_kept():
    assert validate_and_limit("SELECT * FROM vw_active_fleet LIMIT 5").endswith("LIMIT 5")

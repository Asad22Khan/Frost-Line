"""Smoke-test the three agent tools against the live services (Neon, Open-Meteo, Pinecone).

Usage:  python scripts/check_agent_tools.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.agent_tools import build_tools  # noqa: E402

CASES = [
    ("query_telemetry_db", {"sql_query": "SELECT risk_classification, COUNT(*) AS readings, "
                                         "ROUND(AVG(cargo_temp_c)::numeric, 2) AS avg_temp_c "
                                         "FROM vw_active_fleet GROUP BY 1 ORDER BY 2 DESC"}),
    ("query_telemetry_db", {"sql_query": "SELECT event_time, latitude, longitude, cargo_temp_c FROM vw_active_fleet "
                                         "WHERE cargo_temp_c > 4 ORDER BY event_time DESC LIMIT 3"}),
    ("query_telemetry_db", {"sql_query": "SELECT * FROM tbl_sc_fleet_hist_raw"}),          # firewall
    ("query_telemetry_db", {"sql_query": "SELECT cargo_temperature FROM vw_active_fleet"}),  # typo -> DB error
    ("fetch_corridor_conditions", {"latitude": 33.77, "longitude": -118.19}),
    ("search_compliance_sop", {"question": "What is the temperature rule for fresh perishables?"}),
    ("search_compliance_sop", {"question": "What is our vacation policy?"}),
]


def main() -> None:
    tools = {t.name: t for t in build_tools()}
    print("=== query_telemetry_db description (generated from the database) ===")
    print(tools["query_telemetry_db"].description[:900], "...\n")
    for name, args in CASES:
        print(f"=== {name}({args}) ===")
        print(tools[name].invoke(args), "\n")


if __name__ == "__main__":
    main()

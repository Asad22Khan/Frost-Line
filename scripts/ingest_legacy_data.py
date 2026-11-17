"""Phase 0: load the fleet telemetry CSV into PostgreSQL as the client's legacy table.

Column names are deliberately mapped to a terse, 2000s-style legacy schema. That is what a real
client system looks like, and the semantic view built in Phase 2 translates them back to plain English.

Usage:  python scripts/ingest_legacy_data.py
"""

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.types import DateTime, Float, VARCHAR

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.db import get_engine, wait_until_ready  # noqa: E402

CSV_PATH = config.DATA_DIR / "raw" / "dynamic_supply_chain_logistics_dataset.csv"
SCHEMA = "public"
TABLE = "tbl_sc_fleet_hist_raw"

LEGACY_COLUMNS = {
    "timestamp": "ts_utc",
    "vehicle_gps_latitude": "v_lat",
    "vehicle_gps_longitude": "v_lon",
    "fuel_consumption_rate": "fuel_cons_rt",
    "eta_variation_hours": "eta_var_hrs",
    "traffic_congestion_level": "trf_cng_lvl",
    "warehouse_inventory_level": "wh_inv_lvl",
    "loading_unloading_time": "ld_uld_tm",
    "handling_equipment_availability": "hdl_eqp_avl",
    "order_fulfillment_status": "ord_ful_sts",
    "weather_condition_severity": "wx_sev_idx",
    "port_congestion_level": "prt_cng_lvl",
    "shipping_costs": "shp_cost_amt",
    "supplier_reliability_score": "sup_rel_scr",
    "lead_time_days": "lead_tm_days",
    "historical_demand": "hist_dmd_qty",
    "iot_temperature": "iot_temp_val_c",
    "cargo_condition_status": "cgo_cond_cd",
    "route_risk_level": "rt_rsk_idx",
    "customs_clearance_time": "cust_clr_tm",
    "driver_behavior_score": "drv_bhv_scr",
    "fatigue_monitoring_score": "drv_ftg_scr",
    "disruption_likelihood_score": "disr_lkl_scr",
    "delay_probability": "delay_prob_dec",
    "risk_classification": "risk_cls_txt",
    "delivery_time_deviation": "dlv_tm_dev",
}


def copy_rows(table, conn, keys, data_iter):
    """pandas.to_sql insert method that streams rows with PostgreSQL COPY (much faster than INSERTs)."""
    columns = ", ".join(keys)
    target = f"{table.schema}.{table.name}" if table.schema else table.name
    with conn.connection.driver_connection.cursor() as cur:
        with cur.copy(f"COPY {target} ({columns}) FROM STDIN") as copy:
            for row in data_iter:
                copy.write_row(row)


def main() -> None:
    print(f"Reading {CSV_PATH.name} ...")
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df[list(LEGACY_COLUMNS)].rename(columns=LEGACY_COLUMNS)
    df["sys_ingest_flag"] = "Y"
    print(f"  {len(df):,} rows x {df.shape[1]} columns")

    dtypes = {col: Float() for col in df.columns}
    dtypes.update({"ts_utc": DateTime(), "risk_cls_txt": VARCHAR(20), "sys_ingest_flag": VARCHAR(1)})

    engine = get_engine("admin")
    print(f"Connecting to {engine.url.host}/{engine.url.database} ...")
    wait_until_ready(engine)

    print(f"Writing {SCHEMA}.{TABLE} ...")
    df.to_sql(TABLE, engine, schema=SCHEMA, if_exists="replace", index=False, dtype=dtypes, method=copy_rows)

    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {SCHEMA}.{TABLE}")).scalar_one()
    print(f"Legacy ingestion complete: {count:,} rows in {SCHEMA}.{TABLE}")


if __name__ == "__main__":
    main()

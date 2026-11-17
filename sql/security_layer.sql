-- =====================================================================
-- FrostLine - Phase 2: semantic layer + least-privilege access for the AI agent
--
-- Run through scripts/setup_security.py. That script creates the role
-- fde_agent_ro with a generated password first, then executes this file
-- as the database owner. Safe to re-run.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Semantic layer: a clean, documented view over the legacy table.
--    The agent only ever sees these plain-English column names.
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS fde_views;

DROP VIEW IF EXISTS fde_views.vw_active_fleet;

CREATE VIEW fde_views.vw_active_fleet AS
SELECT
    ts_utc          AS event_time,
    v_lat           AS latitude,
    v_lon           AS longitude,
    iot_temp_val_c  AS cargo_temp_c,
    cgo_cond_cd     AS cargo_condition_score,
    risk_cls_txt    AS risk_classification,
    delay_prob_dec  AS delay_probability,
    disr_lkl_scr    AS disruption_likelihood,
    rt_rsk_idx      AS route_risk_level,
    prt_cng_lvl     AS port_congestion_level,
    trf_cng_lvl     AS traffic_congestion_level,
    wx_sev_idx      AS weather_severity,
    eta_var_hrs     AS eta_variation_hours,
    dlv_tm_dev      AS delivery_time_deviation,
    fuel_cons_rt    AS fuel_consumption_rate,
    drv_bhv_scr     AS driver_behavior_score,
    drv_ftg_scr     AS driver_fatigue_score,
    cust_clr_tm     AS customs_clearance_time,
    ld_uld_tm       AS loading_unloading_time,
    hdl_eqp_avl     AS handling_equipment_availability,
    ord_ful_sts     AS order_fulfillment_status,
    wh_inv_lvl      AS warehouse_inventory_level,
    shp_cost_amt    AS shipping_cost,
    sup_rel_scr     AS supplier_reliability_score,
    lead_tm_days    AS lead_time_days,
    hist_dmd_qty    AS historical_demand
FROM public.tbl_sc_fleet_hist_raw;

COMMENT ON VIEW fde_views.vw_active_fleet IS
    'Hourly fleet telemetry snapshots, Southern California, 2021-01-01 to 2024-08-29. One row per vehicle reading; there is no vehicle or shipment ID.';

COMMENT ON COLUMN fde_views.vw_active_fleet.event_time                      IS 'Timestamp of the reading (UTC, hourly)';
COMMENT ON COLUMN fde_views.vw_active_fleet.latitude                        IS 'Vehicle GPS latitude';
COMMENT ON COLUMN fde_views.vw_active_fleet.longitude                       IS 'Vehicle GPS longitude';
COMMENT ON COLUMN fde_views.vw_active_fleet.cargo_temp_c                    IS 'IoT cargo temperature in degrees Celsius (fresh perishables must stay between 0 and 4)';
COMMENT ON COLUMN fde_views.vw_active_fleet.cargo_condition_score           IS 'Cargo condition, 0 (poor) to 1 (good)';
COMMENT ON COLUMN fde_views.vw_active_fleet.risk_classification             IS 'One of: Low Risk, Moderate Risk, High Risk';
COMMENT ON COLUMN fde_views.vw_active_fleet.delay_probability               IS 'Probability of delay, 0 to 1';
COMMENT ON COLUMN fde_views.vw_active_fleet.disruption_likelihood           IS 'Likelihood of a supply-chain disruption, 0 to 1';
COMMENT ON COLUMN fde_views.vw_active_fleet.route_risk_level                IS 'Route risk index, 0 (safe) to 10 (high risk)';
COMMENT ON COLUMN fde_views.vw_active_fleet.port_congestion_level           IS 'Port congestion severity index, 0 to 10';
COMMENT ON COLUMN fde_views.vw_active_fleet.traffic_congestion_level        IS 'Road traffic congestion level, 0 to 10';
COMMENT ON COLUMN fde_views.vw_active_fleet.weather_severity                IS 'Weather condition severity, 0 (clear) to 1 (severe)';
COMMENT ON COLUMN fde_views.vw_active_fleet.eta_variation_hours             IS 'Difference between estimated and actual arrival time, in hours';
COMMENT ON COLUMN fde_views.vw_active_fleet.delivery_time_deviation         IS 'Deviation from the scheduled delivery time, in hours';
COMMENT ON COLUMN fde_views.vw_active_fleet.fuel_consumption_rate           IS 'Fuel consumption rate (litres per hour)';
COMMENT ON COLUMN fde_views.vw_active_fleet.driver_behavior_score           IS 'Driver behaviour score, 0 (poor) to 1 (excellent)';
COMMENT ON COLUMN fde_views.vw_active_fleet.driver_fatigue_score            IS 'Driver fatigue score, 0 (alert) to 1 (fatigued)';
COMMENT ON COLUMN fde_views.vw_active_fleet.customs_clearance_time          IS 'Customs clearance time, in hours';
COMMENT ON COLUMN fde_views.vw_active_fleet.loading_unloading_time          IS 'Loading/unloading time, in hours';
COMMENT ON COLUMN fde_views.vw_active_fleet.handling_equipment_availability IS 'Handling equipment (e.g. forklifts) availability, 0 (unavailable) to 1 (available)';
COMMENT ON COLUMN fde_views.vw_active_fleet.order_fulfillment_status        IS 'Order fulfillment, 0 (not fulfilled) to 1 (fulfilled)';
COMMENT ON COLUMN fde_views.vw_active_fleet.warehouse_inventory_level       IS 'Warehouse inventory level (units)';
COMMENT ON COLUMN fde_views.vw_active_fleet.shipping_cost                   IS 'Shipping cost (USD)';
COMMENT ON COLUMN fde_views.vw_active_fleet.supplier_reliability_score      IS 'Supplier reliability, 0 to 1';
COMMENT ON COLUMN fde_views.vw_active_fleet.lead_time_days                  IS 'Average supplier lead time, in days';
COMMENT ON COLUMN fde_views.vw_active_fleet.historical_demand               IS 'Historical demand (units)';

-- ---------------------------------------------------------------------
-- 2. Least privilege: the agent may read the view and nothing else.
-- ---------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, fde_agent_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA fde_views TO fde_agent_ro;
GRANT SELECT ON fde_views.vw_active_fleet TO fde_agent_ro;

-- ---------------------------------------------------------------------
-- 3. Role-level guard rails (defence in depth on top of the grants).
-- ---------------------------------------------------------------------
ALTER ROLE fde_agent_ro SET default_transaction_read_only = on;
ALTER ROLE fde_agent_ro SET statement_timeout = '15s';
ALTER ROLE fde_agent_ro SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE fde_agent_ro SET search_path = fde_views;
ALTER ROLE fde_agent_ro CONNECTION LIMIT 5;

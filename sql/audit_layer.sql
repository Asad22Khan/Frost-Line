-- =====================================================================
-- FrostLine - Phase 5: append-only audit log for every agent step
--
-- Run through scripts/setup_audit.py. That script creates the role
-- fde_audit_app with a generated password first, then executes this file
-- as the database owner. Safe to re-run.
--
-- Separation of duties:
--   fde_agent_ro   runs the SQL the LLM writes. It cannot see this schema at all.
--   fde_audit_app  used by application code only. It can INSERT and SELECT log rows,
--                  but can never UPDATE, DELETE or TRUNCATE them.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS fde_audit;

CREATE TABLE IF NOT EXISTS fde_audit.agent_audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    logged_at   timestamptz NOT NULL DEFAULT now(),
    session_id  text        NOT NULL,
    turn_id     text        NOT NULL,
    event_type  text        NOT NULL CHECK (event_type IN
                    ('question', 'tool_call', 'tool_result', 'answer', 'blocked', 'error')),
    node        text,
    tool_name   text,
    content     text,
    model       text,
    latency_ms  integer
);

CREATE INDEX IF NOT EXISTS ix_audit_logged_at ON fde_audit.agent_audit_log (logged_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_session   ON fde_audit.agent_audit_log (session_id, logged_at);

COMMENT ON TABLE fde_audit.agent_audit_log IS
    'Append-only trail of every FrostLine agent step: user question, each tool call and result, final answer, '
    'blocked inputs and errors. Written by application code, never by the LLM.';

-- ---------------------------------------------------------------------
-- Least privilege
-- ---------------------------------------------------------------------
REVOKE ALL ON SCHEMA fde_audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA fde_audit FROM PUBLIC, fde_agent_ro, fde_audit_app;

GRANT USAGE ON SCHEMA fde_audit TO fde_audit_app;
GRANT INSERT, SELECT ON fde_audit.agent_audit_log TO fde_audit_app;

-- No access to the telemetry data or the raw table.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM fde_audit_app;
REVOKE ALL ON ALL TABLES IN SCHEMA fde_views FROM fde_audit_app;

-- ---------------------------------------------------------------------
-- Append-only, even if a grant is added by mistake later:
-- a trigger rejects any UPDATE or DELETE on the log.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fde_audit.reject_audit_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'agent_audit_log is append-only: % is not allowed', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_append_only ON fde_audit.agent_audit_log;
CREATE TRIGGER trg_audit_append_only
    BEFORE UPDATE OR DELETE ON fde_audit.agent_audit_log
    FOR EACH ROW EXECUTE FUNCTION fde_audit.reject_audit_change();

DROP TRIGGER IF EXISTS trg_audit_no_truncate ON fde_audit.agent_audit_log;
CREATE TRIGGER trg_audit_no_truncate
    BEFORE TRUNCATE ON fde_audit.agent_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION fde_audit.reject_audit_change();

-- ---------------------------------------------------------------------
-- Role guard rails
-- ---------------------------------------------------------------------
ALTER ROLE fde_audit_app SET statement_timeout = '10s';
ALTER ROLE fde_audit_app SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE fde_audit_app SET search_path = fde_audit;
ALTER ROLE fde_audit_app CONNECTION LIMIT 5;

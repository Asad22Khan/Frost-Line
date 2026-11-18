"""Append-only audit trail of agent activity, stored in the client's own database (fde_audit schema).

Written by application code through the dedicated `fde_audit_app` role, never by the LLM and never through
the agent's role. Events of one question are buffered and written in a single round trip at the end of the
turn, including when the turn fails, so logging adds almost no latency to the chat.
"""

import logging
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import text

from src.db import get_engine

log = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 8000
EVENT_TYPES = ("question", "tool_call", "tool_result", "answer", "blocked", "error")


@dataclass
class AuditTrail:
    session_id: str
    turn_id: str
    events: list[dict] = field(default_factory=list)

    def add(self, event_type: str, content: str = "", node: str | None = None, tool_name: str | None = None,
            model: str | None = None, latency_ms: int | None = None) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown audit event type: {event_type}")
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + f" ... [truncated, {len(content)} chars total]"
        self.events.append({
            "session_id": self.session_id, "turn_id": self.turn_id, "event_type": event_type, "node": node,
            "tool_name": tool_name, "content": content, "model": model, "latency_ms": latency_ms,
        })

    def flush(self) -> bool:
        """Write buffered events. Returns False (and keeps the chat working) if the audit DB is unreachable."""
        if not self.events:
            return True
        try:
            with get_engine("audit").begin() as conn:
                conn.execute(
                    text("INSERT INTO fde_audit.agent_audit_log "
                         "(session_id, turn_id, event_type, node, tool_name, content, model, latency_ms) "
                         "VALUES (:session_id, :turn_id, :event_type, :node, :tool_name, :content, :model, :latency_ms)"),
                    self.events,
                )
            self.events.clear()
            return True
        except Exception as exc:  # never let logging break the conversation
            log.error("Audit log write failed (%s events kept in memory): %s", len(self.events), exc)
            return False


def read_audit_log(limit: int = 500, session_id: str | None = None, event_types: list[str] | None = None,
                   since_hours: int | None = None) -> pd.DataFrame:
    clauses, params = [], {"limit": limit}
    if session_id:
        clauses.append("session_id = :session_id")
        params["session_id"] = session_id
    if event_types:
        clauses.append("event_type = ANY(:event_types)")
        params["event_types"] = list(event_types)
    if since_hours:
        clauses.append("logged_at >= now() - make_interval(hours => :since_hours)")
        params["since_hours"] = since_hours
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = text(
        "SELECT id, logged_at, session_id, turn_id, event_type, node, tool_name, model, latency_ms, content "
        f"FROM fde_audit.agent_audit_log {where} ORDER BY id DESC LIMIT :limit"
    )
    with get_engine("audit").connect() as conn:
        return pd.read_sql(query, conn, params=params)


def audit_summary(since_hours: int = 24) -> dict:
    query = text(
        """
        SELECT
            COUNT(DISTINCT session_id)                                   AS sessions,
            COUNT(*) FILTER (WHERE event_type = 'question')              AS questions,
            COUNT(*) FILTER (WHERE event_type = 'tool_call')             AS tool_calls,
            COUNT(*) FILTER (WHERE event_type = 'blocked')               AS blocked,
            COUNT(*) FILTER (WHERE event_type = 'error')                 AS errors,
            COUNT(*) FILTER (WHERE event_type = 'tool_result'
                             AND content LIKE :rejected)                 AS rejected_sql,
            ROUND(AVG(latency_ms) FILTER (WHERE event_type = 'answer'))  AS avg_answer_ms
        FROM fde_audit.agent_audit_log
        WHERE logged_at >= now() - make_interval(hours => :h)
        """
    )
    with get_engine("audit").connect() as conn:
        row = conn.execute(query, {"h": since_hours, "rejected": "QUERY REJECTED%"}).mappings().one()
    return {k: (int(v) if v is not None else 0) for k, v in row.items()}

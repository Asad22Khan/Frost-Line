"""Append-only audit trail of agent activity, stored in the client's own database (fde_audit schema).

Written by application code through the dedicated `fde_audit_app` role, never by the LLM and never through
the agent's role. Events of one question are buffered and written in a single round trip at the end of the
turn, including when the turn fails, so logging adds almost no latency to the chat.

Vocabulary
  session_id     the visitor (kept in the page URL as ?v=..., so a refresh keeps it)
  thread_id      one conversation; "New conversation" starts a new one
  turn_id        one question and everything the agent did to answer it
"""

import logging
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import text

from src.db import get_engine

log = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 8000
MAX_LABEL_CHARS = 60
EVENT_TYPES = ("question", "tool_call", "tool_result", "answer", "blocked", "error")
COLUMNS = ("id, logged_at, session_id, visitor_label, thread_id, turn_id, event_type, node, tool_name, model, "
           "latency_ms, content")


def clean_label(label: str | None) -> str | None:
    """Visitor name/company: single line, trimmed, length-limited. None if empty."""
    if not label:
        return None
    label = " ".join(label.split())[:MAX_LABEL_CHARS]
    return label or None


@dataclass
class AuditTrail:
    session_id: str
    turn_id: str
    thread_id: str | None = None
    visitor_label: str | None = None
    events: list[dict] = field(default_factory=list)

    def add(self, event_type: str, content: str = "", node: str | None = None, tool_name: str | None = None,
            model: str | None = None, latency_ms: int | None = None) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown audit event type: {event_type}")
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + f" ... [truncated, {len(content)} chars total]"
        self.events.append({
            "session_id": self.session_id, "thread_id": self.thread_id, "turn_id": self.turn_id,
            "visitor_label": clean_label(self.visitor_label), "event_type": event_type, "node": node,
            "tool_name": tool_name, "content": content, "model": model, "latency_ms": latency_ms,
        })

    def flush(self) -> bool:
        """Write buffered events. Returns False (and keeps the chat working) if the audit DB is unreachable."""
        if not self.events:
            return True
        try:
            with get_engine("audit").begin() as conn:
                conn.execute(
                    text("INSERT INTO fde_audit.agent_audit_log (session_id, thread_id, turn_id, visitor_label, "
                         "event_type, node, tool_name, content, model, latency_ms) VALUES (:session_id, "
                         ":thread_id, :turn_id, :visitor_label, :event_type, :node, :tool_name, :content, :model, "
                         ":latency_ms)"),
                    self.events,
                )
            self.events.clear()
            return True
        except Exception as exc:  # never let logging break the conversation
            log.error("Audit log write failed (%s events kept in memory): %s", len(self.events), exc)
            return False


def _read(query: str, params: dict) -> pd.DataFrame:
    with get_engine("audit").connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def read_audit_log(limit: int = 500, session_id: str | None = None, event_types: list[str] | None = None,
                   since_hours: int | None = None) -> pd.DataFrame:
    """Newest events first. Pass session_id to restrict to one visitor."""
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
    return _read(f"SELECT {COLUMNS} FROM fde_audit.agent_audit_log {where} ORDER BY id DESC LIMIT :limit", params)


def read_session_events(session_id: str) -> pd.DataFrame:
    """Every event of one visitor, oldest first (used to rebuild their conversations)."""
    return _read(f"SELECT {COLUMNS} FROM fde_audit.agent_audit_log WHERE session_id = :s ORDER BY id LIMIT 5000",
                 {"s": session_id})


def list_sessions(since_hours: int | None = None, limit: int = 300) -> pd.DataFrame:
    """One row per visitor: name if given, activity window, counts and their first question."""
    where = "WHERE logged_at >= now() - make_interval(hours => :h)" if since_hours else ""
    return _read(
        f"""
        SELECT session_id,
               (array_agg(visitor_label ORDER BY id DESC) FILTER (WHERE visitor_label IS NOT NULL))[1]
                                                               AS visitor,
               MIN(logged_at)                                  AS first_seen,
               MAX(logged_at)                                  AS last_seen,
               COUNT(*) FILTER (WHERE event_type = 'question') AS questions,
               COUNT(*) FILTER (WHERE event_type = 'blocked')  AS blocked,
               COUNT(DISTINCT thread_id)                       AS conversations,
               (array_agg(content ORDER BY id) FILTER (WHERE event_type = 'question'))[1]
                                                               AS first_question
        FROM fde_audit.agent_audit_log {where}
        GROUP BY session_id
        ORDER BY MAX(logged_at) DESC
        LIMIT :limit
        """,
        {"h": since_hours, "limit": limit},
    )


def latest_thread(session_id: str) -> tuple[str | None, str | None, pd.DataFrame]:
    """(thread_id, visitor_label, events oldest first) of the visitor's most recent conversation."""
    events = read_session_events(session_id)
    if events.empty:
        return None, None, events
    labels = events["visitor_label"].dropna()
    label = labels.iloc[-1] if not labels.empty else None
    threads = events["thread_id"].dropna()
    if threads.empty:
        return None, label, events.iloc[0:0]
    thread_id = threads.iloc[-1]
    return thread_id, label, events[events["thread_id"] == thread_id]


def audit_summary(since_hours: int = 24) -> dict:
    """Counts only, no content: safe to show every visitor."""
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

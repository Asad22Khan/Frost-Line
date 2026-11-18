"""Audit Log: who asked what, which tools ran with which inputs, and what came back.

Access needs AUDIT_PAGE_PASSWORD (an app-level secret). The page reads the log through the append-only
fde_audit_app role, never with the database owner's credentials.
"""

import hmac
import time

import streamlit as st

from src.audit import EVENT_TYPES, audit_summary, read_audit_log
from src.config import env

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
WINDOWS = {"Last hour": 1, "Last 24 hours": 24, "Last 7 days": 24 * 7, "Last 30 days": 24 * 30, "All time": None}


def _login(expected: str) -> None:
    locked_until = st.session_state.get("audit_locked_until", 0)
    if time.time() < locked_until:
        st.error(f"Too many failed attempts. Try again in {int(locked_until - time.time())} s.")
        return
    with st.form("audit_login"):
        password = st.text_input("Audit password", type="password")
        if st.form_submit_button("Unlock", type="primary"):
            if hmac.compare_digest(password.encode(), expected.encode()):
                st.session_state.audit_unlocked = True
                st.session_state.audit_failures = 0
                st.rerun()
            failures = st.session_state.get("audit_failures", 0) + 1
            st.session_state.audit_failures = failures
            if failures >= MAX_FAILED_ATTEMPTS:
                st.session_state.audit_locked_until = time.time() + LOCKOUT_SECONDS
                st.session_state.audit_failures = 0
            st.error("Incorrect password.")


def _metrics(hours: int) -> None:
    s = audit_summary(hours)
    cols = st.columns(7)
    cols[0].metric("Sessions", s["sessions"])
    cols[1].metric("Questions", s["questions"])
    cols[2].metric("Tool calls", s["tool_calls"])
    cols[3].metric("Inputs blocked", s["blocked"])
    cols[4].metric("SQL rejected", s["rejected_sql"])
    cols[5].metric("Errors", s["errors"])
    cols[6].metric("Avg answer", f"{s['avg_answer_ms'] / 1000:.1f} s" if s["avg_answer_ms"] else "–")


def audit_page() -> None:
    st.title("Security & Audit Log")
    st.caption("Append-only record of every question, tool call, tool result, answer, blocked input and error. "
               "Stored in the client's own database; rows can be added but never edited or deleted.")

    expected = env("AUDIT_PAGE_PASSWORD")
    if not expected:
        st.info("The audit page is disabled. Set AUDIT_PAGE_PASSWORD in the environment to enable it.")
        return
    if not st.session_state.get("audit_unlocked"):
        _login(expected)
        return

    top = st.columns([3, 1])
    window = top[0].segmented_control("Time window", list(WINDOWS), default="Last 24 hours") or "Last 24 hours"
    if top[1].button("Lock", icon="🔒", width="stretch"):
        st.session_state.audit_unlocked = False
        st.rerun()
    hours = WINDOWS[window]

    try:
        _metrics(hours or 24 * 365 * 10)
        f1, f2, f3 = st.columns([2, 2, 1])
        types = f1.multiselect("Event types", EVENT_TYPES, default=[])
        session = f2.text_input("Session ID", placeholder="all sessions")
        if f3.toggle("This session", value=False):
            session = st.session_state.session_id
        limit = 1000
        df = read_audit_log(limit=limit, session_id=session.strip() or None, event_types=types or None,
                            since_hours=hours)
    except Exception as exc:
        st.error(f"Could not read the audit log ({exc.__class__.__name__}). Check AUDIT_DATABASE_URL.")
        return

    st.caption(f"{len(df)} event(s){' (latest ' + str(limit) + ')' if len(df) == limit else ''}")
    st.dataframe(
        df, hide_index=True, width="stretch", height=480,
        column_config={
            "logged_at": st.column_config.DatetimeColumn("Time (UTC)", format="YYYY-MM-DD HH:mm:ss"),
            "latency_ms": st.column_config.NumberColumn("Latency (ms)"),
            "content": st.column_config.TextColumn("Content", width="large"),
        },
    )
    st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), file_name="frostline_audit_log.csv",
                       mime="text/csv", icon="⬇️")

    if not df.empty:
        st.subheader("Replay a question")
        turns = df.drop_duplicates("turn_id")[["turn_id", "session_id", "logged_at"]]
        questions = df[df.event_type == "question"].set_index("turn_id")["content"].to_dict()
        options = {t: f"{questions.get(t, '(question outside filter)')[:90]}  ·  {s}" for t, s in
                   zip(turns.turn_id, turns.session_id)}
        turn = st.selectbox("Question", list(options), format_func=options.get)
        for _, row in df[df.turn_id == turn].sort_values("id").iterrows():
            label = row.event_type + (f" · {row.tool_name}" if isinstance(row.tool_name, str) else "")
            with st.expander(f"{row.logged_at:%H:%M:%S}  {label}", expanded=row.event_type in ("question", "answer")):
                st.text(row.content)

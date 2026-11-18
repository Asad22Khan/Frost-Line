"""Audit Log with two views.

My activity     public. Shows only the current visitor's own events; the visitor ID comes from server-side
                session state, never from anything typed on this page. Totals for the whole system are shown
                as counts only.
All visitors    unlocked with AUDIT_PAGE_PASSWORD (app owner only). Every visitor, their conversations as
                readable transcripts, and the raw event log with filters and CSV export.

Both read through the append-only fde_audit_app role, never with the database owner's credentials.
"""

import hmac
import time

import pandas as pd
import streamlit as st

from src.audit import EVENT_TYPES, audit_summary, list_sessions, read_audit_log, read_session_events
from src.config import env
from src.ui.transcript import messages_from_events, plural, render_messages

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
WINDOWS = {"Last hour": 1, "Last 24 hours": 24, "Last 7 days": 24 * 7, "Last 30 days": 24 * 30, "All time": None}
MY_VIEW, ALL_VIEW = "👤 My activity", "🔒 All visitors"
EVENT_COLUMNS = {
    "logged_at": st.column_config.DatetimeColumn("Time (UTC)", format="YYYY-MM-DD HH:mm:ss"),
    "latency_ms": st.column_config.NumberColumn("Latency (ms)"),
    "content": st.column_config.TextColumn("Content", width="large"),
}


# ---------------------------------------------------------------- shared pieces
def _metrics(hours: int | None, caption: str) -> None:
    s = audit_summary(hours or 24 * 365 * 10)
    st.caption(caption)
    cols = st.columns(7)
    cols[0].metric("Visitors", s["sessions"])
    cols[1].metric("Questions", s["questions"])
    cols[2].metric("Tool calls", s["tool_calls"])
    cols[3].metric("Inputs blocked", s["blocked"])
    cols[4].metric("SQL rejected", s["rejected_sql"])
    cols[5].metric("Errors", s["errors"])
    cols[6].metric("Avg answer", f"{s['avg_answer_ms'] / 1000:.1f} s" if s["avg_answer_ms"] else "–")


def _conversations(events: pd.DataFrame) -> None:
    """Draw every conversation in these events as a chat transcript, newest conversation first."""
    if events.empty:
        st.info("No activity yet.")
        return
    events = events.sort_values("id")
    threads = events.assign(thread=events["thread_id"].fillna("(before conversations were tracked)"))
    order = threads.groupby("thread", sort=False)["id"].max().sort_values(ascending=False).index
    for n, thread in enumerate(order):
        rows = threads[threads["thread"] == thread]
        messages = messages_from_events(rows)
        started = rows["logged_at"].min()
        title = f"Conversation started {started:%Y-%m-%d %H:%M} UTC · {plural(len(messages) // 2, 'question')}"
        with st.expander(title, expanded=n == 0):
            render_messages(messages)


def _event_table(df: pd.DataFrame, file_name: str) -> None:
    st.caption(plural(len(df), "event"))
    st.dataframe(df, hide_index=True, width="stretch", height=420, column_config=EVENT_COLUMNS)
    st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), file_name=file_name,
                       mime="text/csv", icon="⬇️")


# ---------------------------------------------------------------- public view
def _my_activity(hours: int | None) -> None:
    session_id = st.session_state.session_id
    st.info(f"You are seeing only **your own** activity (visitor `{session_id}`). Other visitors' "
            "conversations are private.", icon="👤")
    _metrics(hours, "Across all visitors (counts only, no content)")

    events = read_session_events(session_id)
    tab_chat, tab_raw = st.tabs(["Your conversations", "Your raw event log"])
    with tab_chat:
        _conversations(events)
    with tab_raw:
        if events.empty:
            st.info("No activity yet. Ask something on the Dispatch Console, then come back here.")
        else:
            _event_table(events.sort_values("id", ascending=False), "frostline_my_activity.csv")


# ---------------------------------------------------------------- owner view
def _login(expected: str) -> None:
    locked_until = st.session_state.get("audit_locked_until", 0)
    if time.time() < locked_until:
        st.error(f"Too many failed attempts. Try again in {int(locked_until - time.time())} s.")
        return
    st.markdown("Every visitor's conversations are private to the app owner.")
    with st.form("audit_login"):
        password = st.text_input("Owner password", type="password")
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


def _visitor_name(row) -> str:
    who = row.visitor if isinstance(row.visitor, str) else "Anonymous"
    return f"{who}  ·  {row.session_id}  ·  {plural(int(row.questions), 'question')}  ·  last seen {row.last_seen:%b %d %H:%M}"


def _all_visitors(hours: int | None) -> None:
    expected = env("AUDIT_PAGE_PASSWORD")
    if not expected:
        st.info("This view is disabled. Set AUDIT_PAGE_PASSWORD in the environment to enable it.")
        return
    if not st.session_state.get("audit_unlocked"):
        _login(expected)
        return
    if st.button("Lock", icon="🔒"):
        st.session_state.audit_unlocked = False
        st.rerun()

    _metrics(hours, "All visitors")
    tab_visitors, tab_raw = st.tabs(["Visitors & conversations", "Raw event log"])

    with tab_visitors:
        sessions = list_sessions(hours)
        if sessions.empty:
            st.info("No visitors in this time window.")
        else:
            st.dataframe(
                sessions, hide_index=True, width="stretch", height=260,
                column_config={
                    "visitor": st.column_config.TextColumn("Visitor (name / company)"),
                    "first_seen": st.column_config.DatetimeColumn("First seen (UTC)", format="MMM DD HH:mm"),
                    "last_seen": st.column_config.DatetimeColumn("Last seen (UTC)", format="MMM DD HH:mm"),
                    "first_question": st.column_config.TextColumn("First question", width="large"),
                },
            )
            labels = {row.session_id: _visitor_name(row) for row in sessions.itertuples()}
            chosen = st.selectbox("Open a visitor's conversations", list(labels), format_func=labels.get)
            if chosen:
                _conversations(read_session_events(chosen))

    with tab_raw:
        f1, f2 = st.columns([2, 2])
        types = f1.multiselect("Event types", EVENT_TYPES, default=[])
        session = f2.text_input("Visitor ID", placeholder="all visitors")
        df = read_audit_log(limit=1000, session_id=session.strip() or None, event_types=types or None,
                            since_hours=hours)
        _event_table(df, "frostline_audit_log.csv")


# ---------------------------------------------------------------- page
def audit_page() -> None:
    st.title("Security & Audit Log")
    st.caption("Append-only record of every question, tool call, tool result, answer, blocked input and error. "
               "Stored in the client's own database; rows can be added but never edited or deleted.")

    c1, c2 = st.columns([1, 2])
    view = c1.segmented_control("View", [MY_VIEW, ALL_VIEW], default=MY_VIEW, key="audit_view") or MY_VIEW
    window = c2.segmented_control("Time window", list(WINDOWS), default="Last 24 hours", key="audit_window")
    hours = WINDOWS[window or "Last 24 hours"]

    try:
        if view == MY_VIEW:
            _my_activity(hours)
        else:
            _all_visitors(hours)
    except Exception as exc:
        st.error(f"Could not read the audit log ({exc.__class__.__name__}). Check AUDIT_DATABASE_URL.")

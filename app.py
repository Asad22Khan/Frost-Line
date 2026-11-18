"""FrostLine web app.   Run:  streamlit run app.py"""

import logging
import re
import uuid

import streamlit as st

from src.audit import latest_thread
from src.chat_service import restore_memory
from src.orchestrator import FALLBACK_MODEL, PRIMARY_MODEL
from src.ui.audit_page import audit_page
from src.ui.chat_page import chat_page
from src.ui.transcript import answered_pairs, messages_from_events

VISITOR_ID = re.compile(r"^[0-9a-f]{12}$")

st.set_page_config(page_title="FrostLine", page_icon="❄️", layout="wide")


def start_visit() -> None:
    """Identify the visitor from ?v= in the URL (so a refresh keeps their history) and restore their latest
    conversation from the audit log. Runs once per browser session."""
    from_url = st.query_params.get("v", "")
    session_id = from_url if VISITOR_ID.match(from_url) else uuid.uuid4().hex[:12]
    st.session_state.session_id = session_id
    st.session_state.thread_id = uuid.uuid4().hex
    st.session_state.messages = []
    if session_id != from_url:
        return  # brand-new visitor: nothing to restore
    try:
        thread_id, _, events = latest_thread(session_id)
        if thread_id:
            messages = messages_from_events(events)
            st.session_state.thread_id = thread_id
            st.session_state.messages = messages
            restore_memory(thread_id, answered_pairs(messages))
    except Exception as exc:  # audit DB unreachable: start fresh rather than fail
        logging.getLogger(__name__).warning("Could not restore visitor %s: %s", session_id, exc)


if "session_id" not in st.session_state:
    start_visit()
st.query_params["v"] = st.session_state.session_id  # keep the visitor ID in the URL on every page

st.markdown(
    """
    <style>
      /* pull the sidebar content up so the brand sits in the top-left corner */
      [data-testid="stSidebarHeader"] { height: 2.25rem; padding-bottom: 0; }
      [data-testid="stSidebarUserContent"] { padding-top: 0; }
      .fl-brand { display: flex; align-items: center; gap: .55rem; margin: -0.4rem 0 0 0; }
      .fl-brand .fl-icon { font-size: 2.4rem; line-height: 1; }
      .fl-brand .fl-name { font-size: 2.5rem; font-weight: 800; line-height: 1; letter-spacing: -0.02em;
                           color: #1f6fb2; }
      .fl-tagline { font-size: .95rem; opacity: .75; margin: .45rem 0 .2rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

console = st.Page(chat_page, title="Dispatch Console", icon="💬", default=True)  # served at /
audit = st.Page(audit_page, title="Audit Log", icon="🛡️", url_path="audit")
page = st.navigation([console, audit], position="hidden")  # drawn below, under the brand

with st.sidebar:
    st.markdown(
        '<div class="fl-brand"><span class="fl-icon">❄️</span><span class="fl-name">FrostLine</span></div>'
        '<div class="fl-tagline">AI copilot for cold-chain logistics</div>',
        unsafe_allow_html=True,
    )
    st.divider()
    visit = {"v": st.session_state.session_id}
    st.page_link(console, label="Dispatch Console", icon="💬", width="stretch", query_params=visit)
    st.page_link(audit, label="Audit Log", icon="🛡️", width="stretch", query_params=visit)
    st.divider()
    if st.button("New conversation", icon="🔄", width="stretch"):
        st.session_state.thread_id = uuid.uuid4().hex
        st.session_state.messages = []
        st.rerun()
    st.caption(f"Visitor `{st.session_state.session_id}`")
    st.caption(f"Model: `{PRIMARY_MODEL}`  \nFallback: `{FALLBACK_MODEL}`")
    st.caption("Fleet data: historical, Jan 2021 – Aug 2024. Weather: live.")

page.run()

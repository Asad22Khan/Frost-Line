"""FrostLine web app.   Run:  streamlit run app.py"""

import uuid

import streamlit as st

from src.orchestrator import FALLBACK_MODEL, PRIMARY_MODEL
from src.ui.audit_page import audit_page
from src.ui.chat_page import chat_page

st.set_page_config(page_title="FrostLine", page_icon="❄️", layout="wide")

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex[:12]
    st.session_state.thread_id = uuid.uuid4().hex
    st.session_state.messages = []

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
    st.page_link(console, label="Dispatch Console", icon="💬", width="stretch")
    st.page_link(audit, label="Audit Log", icon="🛡️", width="stretch")
    st.divider()
    if st.button("New conversation", icon="🔄", width="stretch"):
        st.session_state.thread_id = uuid.uuid4().hex
        st.session_state.messages = []
        st.rerun()
    st.caption(f"Session `{st.session_state.session_id}`")
    st.caption(f"Model: `{PRIMARY_MODEL}`  \nFallback: `{FALLBACK_MODEL}`")
    st.caption("Fleet data: historical, Jan 2021 – Aug 2024. Weather: live.")

page.run()

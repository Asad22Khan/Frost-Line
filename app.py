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

with st.sidebar:
    st.markdown("## ❄️ FrostLine")
    st.caption("AI copilot for cold-chain logistics")

page = st.navigation([
    st.Page(chat_page, title="Dispatch Console", icon="💬", default=True, url_path="console"),
    st.Page(audit_page, title="Audit Log", icon="🛡️", url_path="audit"),
])

with st.sidebar:
    st.divider()
    if st.button("New conversation", icon="🔄", width="stretch"):
        st.session_state.thread_id = uuid.uuid4().hex
        st.session_state.messages = []
        st.rerun()
    st.caption(f"Session `{st.session_state.session_id}`")
    st.caption(f"Model: `{PRIMARY_MODEL}`  \nFallback: `{FALLBACK_MODEL}`")
    st.caption("Fleet data: historical, Jan 2021 – Aug 2024. Weather: live.")

page.run()

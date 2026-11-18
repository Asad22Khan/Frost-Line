"""Dispatch Console: chat with the agent and see every tool it used."""

import json
import time

import streamlit as st

from src.chat_service import run_turn
from src.input_guard import MAX_INPUT_CHARS

RATE_LIMIT = 8            # questions ...
RATE_WINDOW_SECONDS = 60  # ... per minute, per browser session

TOOL_LABELS = {
    "query_telemetry_db": ("🗄️", "Fleet database"),
    "fetch_corridor_conditions": ("🌦️", "Live weather"),
    "search_compliance_sop": ("📘", "SOP library"),
}

EXAMPLES = [
    "Is any cargo near Los Angeles breaching the temperature SOP? Check the weather there too.",
    "I'm a new night-shift dispatcher. When do I escalate to Tier 2?",
    "How many readings are High Risk with delay probability above 0.65?",
    "What does the SOP say to do when port congestion is above 7?",
]


def _md(text: str) -> str:
    """Streamlit renders $...$ as LaTeX; escape dollar signs so prices display as text."""
    return text.replace("$", r"\$")


def _render_step(step: dict) -> None:
    icon, label = TOOL_LABELS.get(step["tool"], ("🔧", step["tool"]))
    st.markdown(f"**{icon} {label}** · `{step['tool']}`")
    args = step.get("args") or {}
    if "sql_query" in args:
        st.code(args["sql_query"], language="sql")
    else:
        st.code(json.dumps(args, ensure_ascii=False), language="json")
    if step.get("output") is not None:
        output = step["output"]
        st.code(output if len(output) < 4000 else output[:4000] + "\n... (truncated)", language=None, wrap_lines=True)


def _render_assistant(msg: dict) -> None:
    steps = msg.get("steps") or []
    if steps:
        with st.expander(f"🔧 {len(steps)} tool call{'s' if len(steps) != 1 else ''}"):
            for step in steps:
                _render_step(step)
    kind = msg.get("kind", "answer")
    if kind == "blocked":
        st.warning(msg["content"], icon="🛡️")
    elif kind == "error":
        st.error(msg["content"])
    else:
        st.markdown(_md(msg["content"]))
    if msg.get("meta"):
        st.caption(msg["meta"])


def _rate_limited() -> bool:
    now = time.time()
    recent = [t for t in st.session_state.get("asked_at", []) if now - t < RATE_WINDOW_SECONDS]
    st.session_state.asked_at = recent
    if len(recent) >= RATE_LIMIT:
        return True
    recent.append(now)
    return False


def _answer(question: str) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(_md(question))

    with st.chat_message("assistant", avatar="❄️"):
        if _rate_limited():
            msg = {"role": "assistant", "kind": "blocked",
                   "content": f"Rate limit reached ({RATE_LIMIT} questions per minute). Please wait a moment."}
            _render_assistant(msg)
            st.session_state.messages.append(msg)
            return

        steps: dict[str, dict] = {}
        final: dict = {"role": "assistant", "kind": "error", "content": "No answer was produced."}
        with st.status("Thinking ...", expanded=False) as status:
            for event in run_turn(question, st.session_state.session_id, st.session_state.thread_id):
                if event["type"] == "tool_call":
                    steps[event["id"]] = {"tool": event["tool"], "args": event["args"], "output": None}
                    icon, label = TOOL_LABELS.get(event["tool"], ("🔧", event["tool"]))
                    status.update(label=f"{icon} Checking {label.lower()} ...")
                elif event["type"] == "tool_result":
                    step = steps.setdefault(event["id"], {"tool": event["tool"], "args": {}})
                    step["output"] = event["content"]
                    _render_step(step)
                elif event["type"] == "answer":
                    final = {"role": "assistant", "kind": "answer", "content": event["content"],
                             "meta": f"{event.get('model') or 'model'} · {event['latency_ms'] / 1000:.1f} s · "
                                     f"{len(steps)} tool call{'s' if len(steps) != 1 else ''}"}
                else:  # blocked | error
                    final = {"role": "assistant", "kind": event["type"], "content": event["content"]}
            done = {"answer": ("complete", f"Done · {len(steps)} tool call{'s' if len(steps) != 1 else ''}"),
                    "blocked": ("error", "Blocked by input guard"), "error": ("error", "Failed")}
            state, label = done[final["kind"]]
            status.update(label=label, state=state, expanded=False)

        final["steps"] = list(steps.values())
        if final["kind"] == "blocked":
            st.warning(final["content"], icon="🛡️")
        elif final["kind"] == "error":
            st.error(final["content"])
        else:
            st.markdown(_md(final["content"]))
            st.caption(final["meta"])
        st.session_state.messages.append(final)


def chat_page() -> None:
    st.title("Dispatch Console")
    st.caption("See every shipment's temperature, risk and rule in one chat. Answers cite the data and SOPs "
               "they come from; expand the tool calls to see exactly what was checked.")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="❄️" if msg["role"] == "assistant" else None):
            if msg["role"] == "user":
                st.markdown(_md(msg["content"]))
            else:
                _render_assistant(msg)

    clicked = None
    if not st.session_state.messages:
        st.markdown("##### Try asking")
        cols = st.columns(2)
        for i, example in enumerate(EXAMPLES):
            if cols[i % 2].button(example, key=f"example_{i}", width="stretch"):
                clicked = example

    typed = st.chat_input("Ask about shipments, temperatures, risk, weather or SOPs ...", max_chars=MAX_INPUT_CHARS)
    question = typed or clicked
    if question:
        _answer(question)

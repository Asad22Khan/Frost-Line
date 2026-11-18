"""Dispatch Console: chat with the agent and see every tool it used."""

import time

import streamlit as st

from src.chat_service import run_turn
from src.input_guard import MAX_INPUT_CHARS
from src.ui.transcript import TOOL_LABELS, answer_meta, md, plural, render_messages, render_step

RATE_LIMIT = 8            # questions ...
RATE_WINDOW_SECONDS = 60  # ... per minute, per browser session

EXAMPLES = [
    "Is any cargo near Los Angeles breaching the temperature SOP? Check the weather there too.",
    "I'm a new night-shift dispatcher. When do I escalate to Tier 2?",
    "How many readings are High Risk with delay probability above 0.65?",
    "What does the SOP say to do when port congestion is above 7?",
]

LOGGING_NOTICE = ("Conversations are logged for audit and quality purposes. "
                  "You can review your own activity on the Audit Log page.")


def _rate_limited() -> bool:
    now = time.time()
    recent = [t for t in st.session_state.get("asked_at", []) if now - t < RATE_WINDOW_SECONDS]
    st.session_state.asked_at = recent
    if len(recent) >= RATE_LIMIT:
        return True
    recent.append(now)
    return False


def _show_final(final: dict) -> None:
    if final["kind"] == "blocked":
        st.warning(final["content"], icon="🛡️")
    elif final["kind"] == "error":
        st.error(final["content"])
    else:
        st.markdown(md(final["content"]))
        st.caption(final["meta"])


def _answer(question: str) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(md(question))

    with st.chat_message("assistant", avatar="❄️"):
        if _rate_limited():
            final = {"role": "assistant", "kind": "blocked", "steps": [],
                     "content": f"Rate limit reached ({RATE_LIMIT} questions per minute). Please wait a moment."}
            _show_final(final)
            st.session_state.messages.append(final)
            return

        steps: dict[str, dict] = {}
        final = {"role": "assistant", "kind": "error", "content": "No answer was produced."}
        with st.status("Thinking ...", expanded=False) as status:
            for event in run_turn(question, st.session_state.session_id, st.session_state.thread_id,
                                  st.session_state.get("visitor_label")):
                if event["type"] == "tool_call":
                    steps[event["id"]] = {"tool": event["tool"], "args": event["args"], "output": None}
                    icon, label = TOOL_LABELS.get(event["tool"], ("🔧", event["tool"]))
                    status.update(label=f"{icon} Checking {label.lower()} ...")
                elif event["type"] == "tool_result":
                    step = steps.setdefault(event["id"], {"tool": event["tool"], "args": {}})
                    step["output"] = event["content"]
                    render_step(step)
                elif event["type"] == "answer":
                    final = {"role": "assistant", "kind": "answer", "content": event["content"],
                             "meta": answer_meta(event.get("model"), event["latency_ms"], len(steps))}
                else:  # blocked | error
                    final = {"role": "assistant", "kind": event["type"], "content": event["content"]}
            done = {"answer": ("complete", f"Done · {plural(len(steps), 'tool call')}"),
                    "blocked": ("error", "Blocked by input guard"), "error": ("error", "Failed")}
            state, label = done[final["kind"]]
            status.update(label=label, state=state, expanded=False)

        final["steps"] = list(steps.values())
        _show_final(final)
        st.session_state.messages.append(final)


def chat_page() -> None:
    st.title("Dispatch Console")
    st.caption("See every shipment's temperature, risk and rule in one chat. Answers cite the data and SOPs "
               "they come from; expand the tool calls to see exactly what was checked.")

    render_messages(st.session_state.messages)

    clicked = None
    if not st.session_state.messages:
        st.markdown("##### Try asking")
        cols = st.columns(2)
        for i, example in enumerate(EXAMPLES):
            if cols[i % 2].button(example, key=f"example_{i}", width="stretch"):
                clicked = example

    with st.bottom:
        typed = st.chat_input("Ask about shipments, temperatures, risk, weather or SOPs ...",
                              max_chars=MAX_INPUT_CHARS)
        st.caption(f"🔒 {LOGGING_NOTICE}")

    question = typed or clicked
    if question:
        _answer(question)

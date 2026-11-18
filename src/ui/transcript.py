"""Turn audit-log rows back into chat messages, and draw chat messages.

Used by the Dispatch Console (to restore a visitor's conversation after a page refresh) and by the
Audit Log (to show conversations as readable transcripts).
"""

import json

import pandas as pd
import streamlit as st

TOOL_LABELS = {
    "query_telemetry_db": ("🗄️", "Fleet database"),
    "fetch_corridor_conditions": ("🌦️", "Live weather"),
    "search_compliance_sop": ("📘", "SOP library"),
}
RESTORED_ERROR = "Something went wrong while answering this question."


def md(text: str) -> str:
    """Streamlit renders $...$ as LaTeX; escape dollar signs so prices display as text."""
    return text.replace("$", r"\$")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def answer_meta(model: str | None, latency_ms: int | float | None, tool_calls: int) -> str:
    seconds = f" · {latency_ms / 1000:.1f} s" if latency_ms and not pd.isna(latency_ms) else ""
    return f"{model or 'model'}{seconds} · {plural(tool_calls, 'tool call')}"


# ---------------------------------------------------------------- audit rows -> messages
def _text(value) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def messages_from_events(events: pd.DataFrame) -> list[dict]:
    """Rebuild UI messages from one conversation's audit rows (any order; sorted by id here)."""
    messages: list[dict] = []
    if events.empty:
        return messages
    events = events.sort_values("id")
    for _, turn in events.groupby("turn_id", sort=False):
        steps: list[dict] = []
        final: dict | None = None
        question = None
        for _, row in turn.iterrows():
            kind = row["event_type"]
            if kind == "question":
                question = _text(row["content"])
            elif kind == "tool_call":
                try:
                    args = json.loads(_text(row["content"]) or "{}")
                except json.JSONDecodeError:
                    args = {"raw": _text(row["content"])}
                steps.append({"tool": _text(row["tool_name"]), "args": args, "output": None})
            elif kind == "tool_result":
                for step in steps:  # results arrive in the same order as their calls
                    if step["tool"] == _text(row["tool_name"]) and step["output"] is None:
                        step["output"] = _text(row["content"])
                        break
            elif kind == "answer":
                final = {"kind": "answer", "content": _text(row["content"]),
                         "meta": answer_meta(_text(row["model"]) or None, row["latency_ms"], len(steps))}
            elif kind == "blocked":
                final = {"kind": "blocked", "content": _text(row["content"])}
            elif kind == "error":
                final = {"kind": "error", "content": RESTORED_ERROR}
        if question is None:
            continue
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "steps": steps,
                         **(final or {"kind": "error", "content": RESTORED_ERROR})})
    return messages


def answered_pairs(messages: list[dict]) -> list[tuple[str, str]]:
    """(question, answer) pairs for answered turns, used to give the agent back its memory."""
    pairs = []
    for user, assistant in zip(messages[::2], messages[1::2]):
        if assistant.get("kind") == "answer":
            pairs.append((user["content"], assistant["content"]))
    return pairs


# ---------------------------------------------------------------- drawing
def render_step(step: dict) -> None:
    icon, label = TOOL_LABELS.get(step["tool"], ("🔧", step["tool"]))
    st.markdown(f"**{icon} {label}** · `{step['tool']}`")
    args = step.get("args") or {}
    if "sql_query" in args:
        st.code(args["sql_query"], language="sql")
    else:
        st.code(json.dumps(args, ensure_ascii=False), language="json")
    if step.get("output") is not None:
        output = step["output"]
        st.code(output if len(output) < 4000 else output[:4000] + "\n... (truncated)", language=None,
                wrap_lines=True)


def render_assistant(msg: dict) -> None:
    steps = msg.get("steps") or []
    if steps:
        with st.expander(f"🔧 {plural(len(steps), 'tool call')}"):
            for step in steps:
                render_step(step)
    kind = msg.get("kind", "answer")
    if kind == "blocked":
        st.warning(msg["content"], icon="🛡️")
    elif kind == "error":
        st.error(msg["content"])
    else:
        st.markdown(md(msg["content"]))
    if msg.get("meta"):
        st.caption(msg["meta"])


def render_messages(messages: list[dict]) -> None:
    for msg in messages:
        with st.chat_message(msg["role"], avatar="❄️" if msg["role"] == "assistant" else None):
            if msg["role"] == "user":
                st.markdown(md(msg["content"]))
            else:
                render_assistant(msg)

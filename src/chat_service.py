"""One chat turn: input guard -> agent graph -> events for the UI, with every step written to the audit log.

The UI never talks to the agent directly. It iterates `run_turn(...)` and renders the events, so the
guard and the audit trail can't be skipped by a UI change.
"""

import json
import time
import uuid
from collections.abc import Iterator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from src.audit import AuditTrail
from src.input_guard import check_input
from src.orchestrator import get_agent, run_config


def _model_name(msg: AIMessage) -> str | None:
    return (msg.response_metadata or {}).get("model_name")


def restore_memory(thread_id: str, pairs: list[tuple[str, str]]) -> bool:
    """Give the agent back the earlier questions and answers of a conversation restored from the audit log
    (after a page refresh or an app restart). Does nothing if the agent still remembers the thread."""
    agent, config = get_agent(), run_config(thread_id)
    if not pairs or agent.get_state(config).values.get("messages"):
        return False
    history = []
    for question, answer in pairs:
        history += [HumanMessage(question), AIMessage(answer)]
    agent.update_state(config, {"messages": history}, as_node="reasoner")
    return True


def run_turn(question: str, session_id: str, thread_id: str, visitor_label: str | None = None) -> Iterator[dict]:
    """Yield events: blocked | tool_call | tool_result | answer | error. Always ends with exactly one of
    blocked, answer or error. The audit trail for the turn is written when the generator finishes."""
    turn_id = uuid.uuid4().hex[:12]
    trail = AuditTrail(session_id=session_id, turn_id=turn_id, thread_id=thread_id, visitor_label=visitor_label)
    started = time.perf_counter()
    trail.add("question", question)

    try:
        guard = check_input(question)
        if not guard.allowed:
            trail.add("blocked", guard.reason, node="input_guard")
            yield {"type": "blocked", "content": guard.reason}
            return

        answer = None
        for update in get_agent().stream(
            {"messages": [HumanMessage(question)]}, run_config(thread_id), stream_mode="updates"
        ):
            for node, payload in update.items():
                for msg in payload["messages"]:
                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        for call in msg.tool_calls:
                            trail.add("tool_call", json.dumps(call["args"], ensure_ascii=False), node=node,
                                      tool_name=call["name"], model=_model_name(msg))
                            yield {"type": "tool_call", "id": call["id"], "tool": call["name"], "args": call["args"]}
                    elif isinstance(msg, ToolMessage):
                        trail.add("tool_result", str(msg.content), node=node, tool_name=msg.name)
                        yield {"type": "tool_result", "id": msg.tool_call_id, "tool": msg.name,
                               "content": str(msg.content)}
                    elif isinstance(msg, AIMessage):
                        answer = msg

        elapsed = int((time.perf_counter() - started) * 1000)
        if answer is None or not str(answer.content).strip():
            raise RuntimeError("The model returned an empty answer.")
        model = _model_name(answer)
        trail.add("answer", str(answer.content), node="reasoner", model=model, latency_ms=elapsed)
        yield {"type": "answer", "content": str(answer.content), "model": model, "latency_ms": elapsed}

    except GraphRecursionError:
        message = "I stopped because this question needed too many steps. Please ask a narrower question."
        trail.add("error", "GraphRecursionError", node="graph")
        yield {"type": "error", "content": message}
    except Exception as exc:
        trail.add("error", f"{exc.__class__.__name__}: {exc}", node="graph")
        yield {"type": "error", "content": f"Something went wrong while answering ({exc.__class__.__name__}). "
                                           "Please try again in a moment."}
    finally:
        trail.flush()

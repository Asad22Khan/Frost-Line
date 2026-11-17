"""Offline tests for the orchestrator's safety logic (no LLM, database or network needed)."""

from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.orchestrator import (
    MAX_TOOL_ROUNDS,
    RECURSION_LIMIT,
    _recent_history,
    run_config,
    system_prompt,
    tool_rounds,
)


def _tool_round(i: int) -> list:
    call_id = f"call_{i}"
    return [
        AIMessage("", tool_calls=[{"name": "query_telemetry_db", "args": {"sql_query": "SELECT 1"}, "id": call_id}]),
        ToolMessage("1", tool_call_id=call_id, name="query_telemetry_db"),
    ]


def test_system_prompt_is_loaded_with_today():
    prompt = system_prompt()
    assert "FrostLine" in prompt
    assert date.today().isoformat() in prompt
    assert "{today}" not in prompt


def test_tool_rounds_counts_only_current_question():
    messages = [HumanMessage("q1"), *_tool_round(1), *_tool_round(2), AIMessage("answer 1"),
                HumanMessage("q2"), *_tool_round(3)]
    assert tool_rounds(messages) == 1


def test_tool_rounds_zero_for_new_question():
    assert tool_rounds([HumanMessage("q1"), *_tool_round(1), AIMessage("a"), HumanMessage("q2")]) == 0


def test_recent_history_keeps_last_turns_and_starts_at_a_question():
    messages = []
    for i in range(10):
        messages += [HumanMessage(f"q{i}"), *_tool_round(i), AIMessage(f"a{i}")]
    recent = _recent_history(messages, turns=3)
    assert isinstance(recent[0], HumanMessage)
    assert recent[0].content == "q7"
    assert sum(isinstance(m, HumanMessage) for m in recent) == 3


def test_recent_history_never_splits_tool_call_from_result():
    messages = []
    for i in range(8):
        messages += [HumanMessage(f"q{i}"), *_tool_round(i), AIMessage(f"a{i}")]
    recent = _recent_history(messages, turns=2)
    call_ids = {c["id"] for m in recent if isinstance(m, AIMessage) for c in m.tool_calls}
    result_ids = {m.tool_call_id for m in recent if isinstance(m, ToolMessage)}
    assert call_ids == result_ids


def test_short_history_is_unchanged():
    messages = [HumanMessage("q"), AIMessage("a")]
    assert _recent_history(messages, turns=6) == messages


def test_recursion_limit_allows_all_tool_rounds_plus_final_answer():
    # each round = reasoner + tools (2 steps), then one more reasoner step for the forced answer
    assert RECURSION_LIMIT > 2 * MAX_TOOL_ROUNDS + 1
    config = run_config("abc")
    assert config["configurable"]["thread_id"] == "abc"
    assert config["recursion_limit"] == RECURSION_LIMIT

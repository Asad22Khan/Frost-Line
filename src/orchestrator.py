"""FrostLine's LangGraph ReAct agent.

    START -> reasoner --(tool calls?)--> tools -> reasoner -> ... -> END

reasoner  the LLM (Groq). It reads the conversation and either requests tool calls or writes the answer.
tools     runs the requested tools (in parallel when several are requested) and adds their results.

Safeguards:
- The system prompt is added on every LLM call, so it can never be missing.
- At most MAX_TOOL_ROUNDS tool rounds per question. After that the LLM must answer with what it has.
- A hard recursion limit stops the graph even if something unexpected loops.
- Only the last HISTORY_TURNS questions are sent to the LLM, which keeps requests within free-tier limits.
- If the primary model fails (rate limit, outage) the request is retried on a smaller fallback model.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.agent_tools import build_tools
from src.config import env, require

PROMPT_FILE = Path(__file__).parent / "prompts" / "system_prompt.md"

PRIMARY_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = env("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")
MAX_TOOL_ROUNDS = 6
RECURSION_LIMIT = 2 * MAX_TOOL_ROUNDS + 4
HISTORY_TURNS = 6

FINAL_ROUND_NOTE = (
    "You have reached the tool-call limit for this question. Do not request more tools. "
    "Answer now using only the tool results above, and say clearly what you could not find out."
)


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def system_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").replace("{today}", date.today().isoformat())


def _chat_model(model: str) -> ChatGroq:
    return ChatGroq(model=model, api_key=require("GROQ_API_KEY"), temperature=0, max_tokens=2048, max_retries=2)


def _current_turn(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Messages since the user's latest question."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]
    return messages


def _recent_history(messages: list[AnyMessage], turns: int = HISTORY_TURNS) -> list[AnyMessage]:
    """The last `turns` questions with everything after them. Cutting at a question keeps every tool
    call together with its result, which the LLM API requires."""
    starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    return messages[starts[-turns]:] if len(starts) > turns else messages


def tool_rounds(messages: list[AnyMessage]) -> int:
    return sum(1 for m in _current_turn(messages) if isinstance(m, AIMessage) and m.tool_calls)


def build_agent(checkpointer=None):
    tools = build_tools()
    primary, fallback = _chat_model(PRIMARY_MODEL), _chat_model(FALLBACK_MODEL)
    with_tools = primary.bind_tools(tools).with_fallbacks([fallback.bind_tools(tools)])
    without_tools = primary.with_fallbacks([fallback])

    def reasoner(state: AgentState) -> dict:
        history = _recent_history(state["messages"])
        prompt = [SystemMessage(system_prompt())] + history
        if tool_rounds(state["messages"]) >= MAX_TOOL_ROUNDS:
            reply = without_tools.invoke(prompt + [SystemMessage(FINAL_ROUND_NOTE)])
        else:
            reply = with_tools.invoke(prompt)
        return {"messages": [reply]}

    graph = StateGraph(AgentState)
    graph.add_node("reasoner", reasoner)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "reasoner")
    graph.add_conditional_edges("reasoner", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "reasoner")
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


@lru_cache(maxsize=1)
def get_agent():
    """One compiled agent per process. Conversations are kept apart by thread_id."""
    return build_agent()


def run_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}

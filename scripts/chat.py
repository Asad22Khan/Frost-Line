"""Chat with the FrostLine agent in the terminal, showing every tool call as it happens.

    python scripts/chat.py                      # interactive; type 'exit' to quit, 'new' for a fresh thread
    python scripts/chat.py "your question"      # ask one question and exit
    python scripts/chat.py --quiet "question"   # final answer only
"""

import sys
import textwrap
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langgraph.errors import GraphRecursionError  # noqa: E402

from src.orchestrator import PRIMARY_MODEL, get_agent, run_config  # noqa: E402


def _preview(text: str, limit: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f" ... [{len(text) - limit} more chars]"


def ask(question: str, thread_id: str, verbose: bool = True) -> str:
    agent = get_agent()
    answer = ""
    try:
        for update in agent.stream(
            {"messages": [HumanMessage(question)]}, run_config(thread_id), stream_mode="updates"
        ):
            for node, payload in update.items():
                for msg in payload["messages"]:
                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        for call in msg.tool_calls:
                            if verbose:
                                print(f"  [{node}] -> calls {call['name']}({call['args']})")
                    elif isinstance(msg, ToolMessage):
                        if verbose:
                            print(f"  [{node}] <- {msg.name} returned:")
                            print(textwrap.indent(_preview(msg.content), "      "))
                    elif isinstance(msg, AIMessage):
                        answer = msg.content
    except GraphRecursionError:
        answer = "I stopped because this question needed too many steps. Please ask a narrower question."
    return answer


def main() -> None:
    args = sys.argv[1:]
    verbose = "--quiet" not in args
    args = [a for a in args if a != "--quiet"]
    thread_id = str(uuid.uuid4())

    if args:
        print(ask(" ".join(args), thread_id, verbose))
        return

    print(f"FrostLine agent ({PRIMARY_MODEL}). Type 'new' for a fresh conversation, 'exit' to quit.")
    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"exit", "quit"}:
            break
        if question.lower() == "new":
            thread_id = str(uuid.uuid4())
            print("Started a new conversation.")
            continue
        if question:
            print(f"\nFrostLine: {ask(question, thread_id, verbose)}")


if __name__ == "__main__":
    main()

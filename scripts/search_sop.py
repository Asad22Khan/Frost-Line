"""Ask the SOP knowledge base a question and see which sections come back, with their sources.

Usage:
  python scripts/search_sop.py "What is the temperature rule for fresh perishables?"
  python scripts/search_sop.py            # interactive mode, type 'exit' to quit
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # print symbols like ° correctly on Windows terminals

from src.sop_store import search_sop  # noqa: E402


def show(question: str, top_k: int = 3) -> None:
    print(f"\nQ: {question}")
    results = search_sop(question, top_k=top_k)
    if not results:
        print("  No matching SOP clauses found.")
    for rank, r in enumerate(results, start=1):
        print(f"\n  #{rank}  score {r['score']}  |  Source: {r['source_file']}  |  Section: {r['section'] or '-'}")
        print(textwrap.indent(textwrap.fill(r["text"], width=100), "      "))


def main() -> None:
    if len(sys.argv) > 1:
        show(" ".join(sys.argv[1:]))
        return
    while (question := input("\nAsk the SOP > ").strip()).lower() not in {"exit", "quit", ""}:
        show(question)


if __name__ == "__main__":
    main()

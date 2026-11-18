"""Screen user messages before they reach the agent.

1. Length limit: very long inputs are a common way to smuggle instructions and burn free-tier tokens.
2. Prompt-injection classifier: Meta's Llama Prompt Guard 2 (86M), served free on Groq. It returns the
   probability that the text is an attack, in about 0.1 s.

This is one layer, not the security boundary: the read-only database role and the SQL firewall still apply
to anything that gets through. If the classifier itself is unavailable the message is allowed (fail open),
so an outage of an optional check does not take the app down; the outcome is recorded in the audit log.
"""

from dataclasses import dataclass
from functools import lru_cache

import groq

from src.config import env, require

GUARD_MODEL = env("PROMPT_GUARD_MODEL", "meta-llama/llama-prompt-guard-2-86m")
BLOCK_THRESHOLD = 0.9
MAX_INPUT_CHARS = 1500


@dataclass
class GuardResult:
    allowed: bool
    reason: str
    score: float | None = None


@lru_cache(maxsize=1)
def _client() -> groq.Groq:
    return groq.Groq(api_key=require("GROQ_API_KEY"), timeout=10, max_retries=1)


def injection_score(message: str) -> float:
    reply = _client().chat.completions.create(model=GUARD_MODEL, messages=[{"role": "user", "content": message}])
    return float(reply.choices[0].message.content.strip())


def check_input(message: str) -> GuardResult:
    if not message.strip():
        return GuardResult(False, "Empty message.")
    if len(message) > MAX_INPUT_CHARS:
        return GuardResult(False, f"Message is too long ({len(message)} characters, limit {MAX_INPUT_CHARS}).")
    try:
        score = injection_score(message)
    except Exception as exc:
        return GuardResult(True, f"Prompt-injection check unavailable ({exc.__class__.__name__}); allowed.")
    if score >= BLOCK_THRESHOLD:
        return GuardResult(False, f"Blocked as a likely prompt-injection attempt (score {score:.3f}).", score)
    return GuardResult(True, f"Passed prompt-injection check (score {score:.3f}).", score)

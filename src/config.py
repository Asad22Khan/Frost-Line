"""Central configuration: every module reads settings from here, never from os.environ directly."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

load_dotenv(PROJECT_ROOT / ".env")


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


def require(name: str) -> str:
    value = env(name)
    if not value:
        raise RuntimeError(f"Missing required setting '{name}'. Add it to your .env file (see .env.example).")
    return value

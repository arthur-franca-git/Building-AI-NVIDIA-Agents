from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_ALLOWED_COMMANDS = (
    "pytest",
    "py -m pytest",
    "python -m pytest",
    "ruff check src tests",
    "py -m ruff check src tests",
    "mypy",
)


@dataclass(frozen=True)
class AgentConfig:
    workspace: Path
    model: str
    base_url: str | None
    backend: str
    max_tokens: int
    request_timeout_seconds: int
    dry_run: bool
    allowed_commands: tuple[str, ...]


def load_config(workspace: str | None = None, dry_run: bool | None = None) -> AgentConfig:
    load_dotenv()

    configured_workspace = workspace or os.getenv("AGENT_WORKSPACE") or "."
    configured_dry_run = dry_run
    if configured_dry_run is None:
        configured_dry_run = os.getenv("AGENT_DRY_RUN", "true").lower() in {"1", "true", "yes"}

    allowed = tuple(
        command.strip()
        for command in os.getenv("AGENT_ALLOWED_COMMANDS", "").split(",")
        if command.strip()
    )

    base_url = os.getenv("OPENAI_BASE_URL") or None
    backend = os.getenv("AGENT_BACKEND", "").strip().lower()
    if not backend:
        backend = "chat" if base_url else "agents"

    return AgentConfig(
        workspace=Path(configured_workspace).expanduser().resolve(),
        model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
        base_url=base_url,
        backend=backend,
        max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "1024")),
        request_timeout_seconds=int(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "120")),
        dry_run=configured_dry_run,
        allowed_commands=allowed or DEFAULT_ALLOWED_COMMANDS,
    )

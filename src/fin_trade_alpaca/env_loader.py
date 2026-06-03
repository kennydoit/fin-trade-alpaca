from __future__ import annotations

from dotenv import load_dotenv
from typing import Optional


def load_environment_for_mode(mode: str, target: Optional[str] = None, env_file: Optional[str] = None) -> None:
    """Load environment variables for the requested mode.

    Behavior:
    - If `env_file == "none"`, skip loading any files and rely on process env (useful for CI/Codespaces).
    - If `env_file` is a path, load that file (override existing vars).
    - If `mode == "github"` and no `env_file` is provided, do not load files (use process env).
    - Otherwise, load `.env` (non-overriding) then `.env.paper` or `.env.live` as before.
    """
    if env_file == "none":
        return

    if env_file:
        load_dotenv(env_file, override=True)
        return

    if mode == "github":
        # Rely on injected process environment for GitHub/Codespaces secrets.
        return

    # default local behavior: load generic .env first, then mode specific file
    load_dotenv(".env", override=False)
    if mode == "paper":
        load_dotenv(".env.paper", override=True)
    else:
        load_dotenv(".env.live", override=True)

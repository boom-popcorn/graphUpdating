from __future__ import annotations

import os
import re
from pathlib import Path


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path: str | Path, *, override: bool = False) -> set[str]:
    r"""Load simple KEY=VALUE entries without shell evaluation.

    The parser deliberately does not expand variables or execute substitutions.
    It accepts optional single/double quotes and the ``\:``/``\/`` escaping
    sometimes introduced when values are copied from Markdown.
    """

    env_path = Path(path)
    if not env_path.is_file():
        return set()
    loaded: set[str] = set()
    for line_number, raw_line in enumerate(
        env_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid environment line {line_number}: missing '='")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"invalid environment name on line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        value = value.replace(r"\:", ":").replace(r"\/", "/")
        if override or name not in os.environ:
            os.environ[name] = value
            loaded.add(name)
    return loaded


def configure_model_environment(project_root: str | Path) -> dict[str, str | bool]:
    """Load a local env file and map common OpenAI-compatible aliases to ODKG."""

    root = Path(project_root)
    configured_path = os.environ.get("ODKG_ENV_FILE")
    env_path = Path(configured_path) if configured_path else root / "key.env"
    loaded = load_env_file(env_path)

    base_url = os.environ.get("ODKG_LLM_BASE_URL") or os.environ.get(
        "OPENAI_API_BASE"
    ) or os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("ODKG_LLM_API_KEY") or os.environ.get(
        "OPENAI_API_KEY"
    ) or os.environ.get("DEEPSEEK_API_KEY")
    model = os.environ.get("ODKG_LLM_MODEL") or os.environ.get("OPENAI_MODEL")
    is_deepseek = bool(base_url and "api.deepseek.com" in base_url.casefold())
    if not model and is_deepseek:
        model = "deepseek-flash"

    if base_url:
        os.environ.setdefault("ODKG_LLM_BASE_URL", base_url)
    if api_key:
        os.environ.setdefault("ODKG_LLM_API_KEY", api_key)
    if model:
        os.environ.setdefault("ODKG_LLM_MODEL", model)

    if is_deepseek and model:
        os.environ.setdefault("ODKG_LLM_THINKING", "disabled")
        os.environ.setdefault("ODKG_LLM_MAX_TOKENS", "4096")
        os.environ.setdefault("ODKG_LLM_TIMEOUT", "180")
        # The current DeepSeek Flash endpoint accepts image input. Pro is text-only.
        if model == "deepseek-flash":
            os.environ.setdefault("ODKG_VLM_BASE_URL", base_url or "")
            if api_key:
                os.environ.setdefault("ODKG_VLM_API_KEY", api_key)
            os.environ.setdefault("ODKG_VLM_MODEL", model)
            os.environ.setdefault("ODKG_VLM_THINKING", "disabled")
            os.environ.setdefault("ODKG_VLM_MAX_TOKENS", "2048")
            os.environ.setdefault("ODKG_VLM_TIMEOUT", "180")

    return {
        "env_file_found": env_path.is_file(),
        "env_file": str(env_path),
        "loaded_variable_count": len(loaded),
        "base_url_configured": bool(base_url),
        "api_key_configured": bool(api_key),
        "model": model or "",
        "vision_configured": bool(os.environ.get("ODKG_VLM_MODEL")),
    }


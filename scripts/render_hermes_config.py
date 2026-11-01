from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


TOOLS = [
    "get_ontology_version",
    "get_evolution_policy",
    "get_graph_status",
    "list_semantic_adapters",
    "get_llm_harness_status",
    "list_versions",
    "get_version_manifest",
    "ingest_and_preview",
    "ingest_and_run",
    "preview_update_cycle",
    "llm_semantic_perception",
    "llm_ontology_evolution",
    "llm_decision_planning",
    "llm_action_execution",
    "run_llm_multi_agent_maritime",
    "run_update_cycle",
    "rollback_latest",
]


def build_config(root: Path, python: Path) -> dict:
    server_env = {
        "PYTHONPATH": str((root / "src").resolve()),
        "ODKG_RUNTIME_DIR": str((root / "runtime" / "hermes-odkg").resolve()),
    }
    for key in (
        "ODKG_NEO4J_URI",
        "ODKG_NEO4J_USER",
        "ODKG_NEO4J_PASSWORD",
        "ODKG_LLM_BASE_URL",
        "ODKG_LLM_MODEL",
        "ODKG_LLM_API_KEY",
        "ODKG_VLM_BASE_URL",
        "ODKG_VLM_MODEL",
        "ODKG_VLM_API_KEY",
    ):
        if value := os.environ.get(key):
            server_env[key] = value
    return {
        "mcp_servers": {
            "ontology_driven_kg": {
                "command": str(python.resolve()),
                "args": ["-m", "odkg.mcp_server"],
                "env": server_env,
                "tools": {"include": TOOLS},
            }
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    python = args.python or root / ".venv-hermes" / "Scripts" / "python.exe"
    if not python.is_file():
        raise SystemExit(f"Python interpreter not found: {python}")
    output = args.output or root / "runtime" / "hermes-home" / "config.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_config(root, python), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output.resolve())


if __name__ == "__main__":
    main()

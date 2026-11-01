from pathlib import Path

import yaml

from odkg.demo import project_root


def test_hermes_config_exposes_only_expected_odkg_tools() -> None:
    config = yaml.safe_load(
        (project_root() / "integrations" / "hermes" / "config.example.yaml")
        .read_text(encoding="utf-8")
    )
    server = config["mcp_servers"]["ontology_driven_kg"]
    assert server["command"].endswith("python.exe")
    assert "enabled" not in server
    assert "protocol" not in server
    assert set(server["tools"]["include"]) == {
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
        "preview_maritime_events",
        "run_maritime_events",
        "run_update_cycle",
        "rollback_latest",
    }


def test_four_ontology_layer_skills_have_valid_frontmatter() -> None:
    expected = {"object-layer", "relation-layer", "rule-layer", "action-layer"}
    skills_root = project_root() / "skills"
    assert {path.name for path in skills_root.iterdir() if path.is_dir()} == expected
    for directory in expected:
        text = (skills_root / directory / "SKILL.md").read_text(encoding="utf-8")
        _, frontmatter, body = text.split("---", maxsplit=2)
        metadata = yaml.safe_load(frontmatter)
        assert metadata["name"].startswith("odkg-")
        assert metadata["metadata"]["odkg"]["layer"] in directory
        assert body.strip()

from odkg.adapters.kgcl import to_kgcl_commands, validate_with_official_parser
from odkg.demo import build_demo_orchestrator, demo_observations


def test_change_set_serializes_to_kgcl_cnl() -> None:
    orchestrator = build_demo_orchestrator()
    facts = orchestrator.perception.perceive(
        demo_observations(), orchestrator.engine.ontology.version
    )
    change_set = orchestrator.evolution.propose(facts, orchestrator.engine.ontology)
    commands = to_kgcl_commands(change_set)
    assert "create https://example.org/odkg/ResearchProject" in commands
    assert any("owl:ObjectProperty" in command for command in commands)
    assert any("rdfs:domain" in command for command in commands)
    assert any("rdfs:range" in command for command in commands)


def test_commands_can_be_checked_by_official_parser_api() -> None:
    parsed: list[str] = []

    def parser(command: str) -> object:
        parsed.append(command)
        return object()

    report = validate_with_official_parser(["create EX:One"], parser=parser)
    assert report.available
    assert report.valid
    assert report.parsed_count == 1
    assert parsed == ["create EX:One"]


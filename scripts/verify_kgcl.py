from __future__ import annotations

import json

from odkg.adapters.kgcl import to_kgcl_commands, validate_with_official_parser
from odkg.demo import build_demo_orchestrator, demo_observations


def main() -> None:
    orchestrator = build_demo_orchestrator()
    facts = orchestrator.perception.perceive(
        demo_observations(), orchestrator.engine.ontology.version
    )
    change_set = orchestrator.evolution.propose(facts, orchestrator.engine.ontology)
    commands = to_kgcl_commands(change_set)
    report = validate_with_official_parser(commands)
    print(
        json.dumps(
            {
                "available": report.available,
                "valid": report.valid,
                "parsed_count": report.parsed_count,
                "errors": report.errors,
                "commands": commands,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report.available or not report.valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

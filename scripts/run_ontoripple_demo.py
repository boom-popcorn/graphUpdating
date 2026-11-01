from __future__ import annotations

import argparse
import json
from pathlib import Path

from odkg.adapters.ontoripple import OntoRippleRequest, propagate, to_och_graph
from odkg.demo import build_demo_orchestrator, demo_observations, project_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the generic fundedBy ontology change through OntoRipple"
    )
    parser.add_argument(
        "--ontoripple-python",
        default="python",
        help="Python executable of the isolated environment containing OntoRipple",
    )
    parser.add_argument("--workdir", type=Path, default=Path("runtime/ontoripple-demo"))
    args = parser.parse_args()

    root = project_root()
    workdir = args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    orchestrator = build_demo_orchestrator()
    facts = orchestrator.perception.perceive(
        demo_observations(), orchestrator.engine.ontology.version
    )
    change_set = orchestrator.evolution.propose(facts, orchestrator.engine.ontology)

    changes_path = workdir / "changes.och.ttl"
    ontology_path = workdir / "new_ontology.ttl"
    to_och_graph(change_set).serialize(destination=changes_path, format="turtle")
    orchestrator.engine.ontology.preview(change_set).serialize(
        destination=ontology_path, format="turtle"
    )

    request = OntoRippleRequest(
        changes_kg=changes_path,
        old_mapping=root / "examples" / "ontoripple" / "old_mapping.ttl",
        old_shapes=root / "examples" / "ontoripple" / "old_shapes.ttl",
        new_ontology=ontology_path,
        output_mapping=workdir / "updated_mapping.ttl",
        output_shapes=workdir / "updated_shapes.ttl",
    )
    report = propagate(request, python=args.ontoripple_python)
    print(
        json.dumps(
            {
                "mapping_triples": report.mapping_triples,
                "shape_triples": report.shape_triples,
                "output_mapping": str(report.output_mapping),
                "output_shapes": str(report.output_shapes),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


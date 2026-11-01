from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import CandidateFact


class EvolutionPolicy(BaseModel):
    """Machine-readable policy for ontology evolution and auto-commit."""

    policy_id: str = "default-v1"
    minimum_fact_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    auto_commit_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    minimum_distinct_sources: int = Field(default=2, ge=1)
    minimum_modalities: int = Field(default=1, ge=1)
    max_auto_commit_operations: int = Field(default=5, ge=1)
    blocked_namespaces: list[str] = Field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> "EvolutionPolicy":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )


class RuleEvaluation(BaseModel):
    fact_id: str
    decision: Literal["accept", "review", "reject"]
    findings: list[str] = Field(default_factory=list)
    distinct_sources: int
    modalities: int


class EvolutionRuleEngine:
    def __init__(self, policy: EvolutionPolicy) -> None:
        self.policy = policy

    def evaluate(self, fact: CandidateFact) -> RuleEvaluation:
        findings: list[str] = []
        sources = {item.source_id for item in fact.evidence}
        modalities = {item.modality for item in fact.evidence}

        if fact.confidence < self.policy.minimum_fact_confidence:
            findings.append(
                f"confidence {fact.confidence:.3f} is below evolution threshold "
                f"{self.policy.minimum_fact_confidence:.3f}"
            )
            decision: Literal["accept", "review", "reject"] = "reject"
        elif any(
            fact.predicate.startswith(namespace)
            or fact.subject_type.startswith(namespace)
            or bool(fact.object_type and fact.object_type.startswith(namespace))
            for namespace in self.policy.blocked_namespaces
        ):
            findings.append("candidate uses a namespace blocked by evolution policy")
            decision = "reject"
        else:
            if len(sources) < self.policy.minimum_distinct_sources:
                findings.append(
                    f"only {len(sources)} distinct source(s); "
                    f"{self.policy.minimum_distinct_sources} required for auto-commit"
                )
            if len(modalities) < self.policy.minimum_modalities:
                findings.append(
                    f"only {len(modalities)} modality/modalities; "
                    f"{self.policy.minimum_modalities} required for auto-commit"
                )
            decision = "review" if findings else "accept"

        return RuleEvaluation(
            fact_id=fact.fact_id,
            decision=decision,
            findings=findings,
            distinct_sources=len(sources),
            modalities=len(modalities),
        )

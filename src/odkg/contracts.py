from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ChangeType(StrEnum):
    ADD_CLASS = "add_class"
    ADD_OBJECT_PROPERTY = "add_object_property"
    ADD_DATA_PROPERTY = "add_data_property"
    ADD_SUBCLASS = "add_subclass"


class TransactionStatus(StrEnum):
    PROPOSED = "proposed"
    PREPARED = "prepared"
    REJECTED = "rejected"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


class Evidence(BaseModel):
    source_id: str
    modality: str
    locator: str | None = None
    excerpt: str | None = None
    extractor_id: str | None = None
    model_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    subject: str
    predicate: str
    object: str
    subject_type: str
    object_type: str | None = None
    object_is_literal: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(min_length=1)


class CandidateFact(Observation):
    fact_id: str = Field(default_factory=lambda: new_id("fact"))
    ontology_version: str


class ChangeOperation(BaseModel):
    operation: ChangeType
    target: str
    domain: str | None = None
    range: str | None = None
    parent: str | None = None
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class OntologyChangeSet(BaseModel):
    change_set_id: str = Field(default_factory=lambda: new_id("change"))
    base_version: str
    operations: list[ChangeOperation] = Field(default_factory=list)
    trigger_fact_ids: list[str] = Field(default_factory=list)
    policy_id: str | None = None
    requires_human_review: bool = False
    rule_findings: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    severity: str
    code: str
    message: str
    focus_node: str | None = None


class ValidationReport(BaseModel):
    conforms: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    engine: str


class UpdateTransaction(BaseModel):
    transaction_id: str = Field(default_factory=lambda: new_id("tx"))
    status: TransactionStatus = TransactionStatus.PROPOSED
    base_version: str
    target_version: str | None = None
    change_set: OntologyChangeSet
    facts: list[CandidateFact]
    validation: ValidationReport | None = None
    requires_human_review: bool = False
    rejection_reason: str | None = None


class ProvenanceRecord(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("event"))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent: str
    action: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    transaction: UpdateTransaction
    feedback: str
    provenance: list[ProvenanceRecord]


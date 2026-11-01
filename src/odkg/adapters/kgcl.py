from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..contracts import ChangeOperation, ChangeType, OntologyChangeSet


@dataclass(slots=True)
class KGCLValidationReport:
    available: bool
    valid: bool
    parsed_count: int = 0
    errors: list[str] = field(default_factory=list)


def to_kgcl_commands(change_set: OntologyChangeSet) -> list[str]:
    """Render the supported change subset as KGCL controlled language."""
    commands: list[str] = []
    for operation in change_set.operations:
        commands.extend(_operation_commands(operation))
    return commands


def validate_with_official_parser(
    commands: list[str], parser: Callable[[str], Any] | None = None
) -> KGCLValidationReport:
    """Validate commands with KGCL's official ``parse_statement`` API."""
    if parser is None:
        try:
            from kgcl_schema.grammar.parser import parse_statement
        except ImportError as exc:
            return KGCLValidationReport(
                available=False,
                valid=False,
                errors=[f"official KGCL parser is unavailable: {exc}"],
            )
        parser = parse_statement
    errors: list[str] = []
    parsed_count = 0
    for index, command in enumerate(commands):
        try:
            parser(command)
            parsed_count += 1
        except Exception as exc:
            errors.append(f"command[{index}] {command!r}: {exc}")
    return KGCLValidationReport(
        available=True,
        valid=not errors,
        parsed_count=parsed_count,
        errors=errors,
    )


def _operation_commands(operation: ChangeOperation) -> list[str]:
    target = operation.target
    if operation.operation == ChangeType.ADD_CLASS:
        return [f"create {target}"]
    if operation.operation == ChangeType.ADD_SUBCLASS:
        if not operation.parent:
            raise ValueError("add_subclass requires parent")
        return [f"create edge {target} rdfs:subClassOf {operation.parent}"]
    if operation.operation in {
        ChangeType.ADD_OBJECT_PROPERTY,
        ChangeType.ADD_DATA_PROPERTY,
    }:
        property_type = (
            "owl:ObjectProperty"
            if operation.operation == ChangeType.ADD_OBJECT_PROPERTY
            else "owl:DatatypeProperty"
        )
        commands = [
            f"create {target}",
            f"create edge {target} rdf:type {property_type}",
        ]
        if operation.domain:
            commands.append(f"create edge {target} rdfs:domain {operation.domain}")
        if operation.range:
            commands.append(f"create edge {target} rdfs:range {operation.range}")
        return commands
    raise ValueError(f"unsupported KGCL operation: {operation.operation}")


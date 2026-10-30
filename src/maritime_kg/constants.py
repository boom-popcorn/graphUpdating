"""Stable identifiers shared by models, rules, and graph storage."""

from urllib.parse import quote


ONTOLOGY_IRI = "https://example.org/maritime-kg/ontology/"
RESOURCE_IRI = "https://example.org/maritime-kg/resource/"


def resource_iri(kind: str, local_id: str) -> str:
    return f"{RESOURCE_IRI}{quote(kind, safe='')}/{quote(local_id, safe='')}"


def vessel_iri(mmsi: str) -> str:
    return resource_iri("vessel", mmsi)


def observation_iri(event_id: str) -> str:
    return resource_iri("observation", event_id)


def event_iri(event_id: str) -> str:
    return resource_iri("event", event_id)


def assertion_iri(fact_id: str) -> str:
    return resource_iri("assertion", fact_id)


def change_set_iri(change_set_id: str) -> str:
    return resource_iri("change-set", change_set_id)


def graph_version_iri(version: int) -> str:
    return resource_iri("graph-version", str(version))


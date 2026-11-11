from __future__ import annotations

from datetime import datetime
from typing import Any

from .ontology import OntologyModel


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SemanticPerceptionAgent:
    name = "semantic_perception_agent"

    def perceive(self, raw: dict[str, Any]) -> dict[str, Any]:
        observations = raw["observations"]
        ais = sorted(
            (item for item in observations if item["source"] == "AIS"),
            key=lambda item: item["timestamp"],
        )
        if len(ais) < 2:
            raise ValueError("the MVP needs at least two AIS points to derive a duration and distance")
        start, end = ais[0]["timestamp"], ais[-1]["timestamp"]
        duration_seconds = int((_parse_time(end) - _parse_time(start)).total_seconds())
        total_distance = round(sum(float(item.get("segmentDistanceNm", 0)) for item in ais), 3)
        speed = float(ais[-1]["speedKn"])
        evidence = [
            {
                "id": item["id"],
                "type": item["source"],
                "subject": item["subject"],
                "timestamp": item["timestamp"],
                "confidence": float(item["confidence"]),
                "source": item["source"],
            }
            for item in observations
        ]
        times = sorted(item["timestamp"] for item in observations)
        return {
            "roundId": raw["roundId"],
            "timestamp": max(times),
            "vesselId": raw["vesselId"],
            "areaId": raw["areaId"],
            "areaSensitive": bool(raw["areaSensitive"]),
            "entities": [
                {"id": raw["vesselId"], "type": "FishingVessel"},
                {"id": raw["areaId"], "type": "SensitiveArea"},
                {"id": raw["newStateId"], "type": "State"},
            ],
            "states": [{
                "id": raw["newStateId"],
                "of": raw["vesselId"],
                "speed": speed,
                "unit": "kn",
                "travelDistance": total_distance,
                "distanceUnit": "nm",
                "durationSeconds": duration_seconds,
                "validFrom": start,
                "validTo": None,
            }],
            "relations": [
                {"s": raw["vesselId"], "p": "locatedIn", "o": raw["areaId"]},
                {"s": raw["vesselId"], "p": "hasState", "o": raw["newStateId"]},
            ],
            "evidence": evidence,
            "unknownSemantics": ["敏感海域持续低速小范围徘徊"],
            "validFrom": start,
            "validTo": end,
            "temporalEntities": [{"id": "T3", "timestamp": end}],
        }


class DecisionPlanningAgent:
    name = "decision_planning_agent"

    def plan(
        self,
        delta: dict[str, Any],
        ontology: OntologyModel,
        graph: dict[str, Any],
        feedback: list[str] | None = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        state = delta["states"][0]
        rule_hits: list[str] = []
        state_type = "UnderWayState"
        rule_1 = ontology.rules.get("R1")
        if rule_1 and state["speed"] < rule_1["when"]["speedLessThan"]:
            rule_hits.append("R1")
            state_type = "LoiteringState"

        risk_matched = (
            "R2" in ontology.rules
            and state_type == ontology.rules["R2"]["when"]["stateType"]
            and delta["areaSensitive"]
        )
        if risk_matched:
            rule_hits.append("R2")

        event_created = False
        rule_3 = ontology.rules.get("R3")
        if risk_matched and rule_3:
            conditions = rule_3["when"]
            event_created = (
                state["durationSeconds"] > conditions["durationSecondsGreaterThan"]
                and state["travelDistance"] < conditions["travelDistanceLessThan"]
            )
            if event_created:
                rule_hits.append("R3")

        state_id = state["id"]
        event_id = "E-017" if event_created else None
        created = [{
            "id": state_id,
            "type": state_type,
            "properties": {
                "speed": state["speed"],
                "travelDistance": state["travelDistance"],
                "durationSeconds": state["durationSeconds"],
                "validFrom": state["validFrom"],
                "validTo": None,
            },
        }]
        assert_types = [{"id": state_id, "type": state_type}]
        relations = [{"s": delta["vesselId"], "p": "hasState", "o": state_id}]
        evidence = []
        expected = [delta["vesselId"], delta["areaId"], "S-017-0", state_id]
        if risk_matched:
            relations.append({"s": delta["vesselId"], "p": "hasPotentialIllegalActivity", "o": delta["areaId"]})
        if event_created:
            created.append({
                "id": event_id,
                "type": "SuspectedIllegalFishingActivity",
                "properties": {"reviewStatus": "pending", "confidence": round(min(item["confidence"] for item in delta["evidence"]), 3)},
            })
            assert_types.append({"id": event_id, "type": "SuspectedIllegalFishingActivity"})
            relations.extend([
                {"s": event_id, "p": "hasSubject", "o": delta["vesselId"]},
                {"s": event_id, "p": "hasObject", "o": delta["areaId"]},
                {"s": event_id, "p": "hasInstant", "o": delta["temporalEntities"][-1]["id"]},
            ])
            evidence = [{"event": event_id, "evidence": item["id"]} for item in delta["evidence"]]
            expected.append(event_id)

        return {
            "planId": "P1",
            "baseOntologyVersion": ontology.version,
            "replanAttempt": attempt,
            "replanFeedback": feedback or [],
            "ruleHits": rule_hits,
            "closeStates": [{"id": "S-017-0", "validTo": state["validFrom"]}],
            "createIndividuals": created,
            "assertTypes": assert_types,
            "assertRelations": relations,
            "attachEvidence": evidence,
            "evidenceRecords": delta["evidence"],
            "temporalValues": delta["temporalEntities"],
            "expectedSubgraph": expected,
        }

    def replan(
        self,
        previous_plan: dict[str, Any],
        validation_report: dict[str, Any],
        delta: dict[str, Any],
        ontology: OntologyModel,
        graph: dict[str, Any],
    ) -> dict[str, Any]:
        return self.plan(
            delta,
            ontology,
            graph,
            feedback=validation_report.get("suggestions", []),
            attempt=int(previous_plan.get("replanAttempt", 1)) + 1,
        )


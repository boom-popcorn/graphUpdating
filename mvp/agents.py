from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import re
from typing import Any

from .ontology import OntologyModel
from .text import TextPerceptionAgent


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SemanticPerceptionAgent:
    name = "semantic_perception_agent"

    def __init__(self, text_agent: TextPerceptionAgent | None = None):
        self.text_agent = text_agent or TextPerceptionAgent()

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
        delta = {
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
        if "texts" in raw:
            text_delta = self.text_agent.perceive(raw["texts"], raw["vesselId"], start, end,
                                                 raw.get("textToleranceSeconds", 120))
            text_evidence = text_delta.pop("evidence")
            if {item["id"] for item in evidence} & {item["id"] for item in text_evidence}:
                raise ValueError("text and trajectory evidence IDs must be distinct")
            delta.update(text_delta)
            delta["evidence"].extend(text_evidence)
            delta["modalities"] = ["trajectory"] + (["text"] if text_evidence else [])
        return delta


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

        text_summary = self._plan_text(delta, ontology, created, assert_types, relations, evidence,
                                       expected, rule_hits, event_id, state_type)

        return {
            "planId": "P1",
            "baseOntologyVersion": ontology.version,
            "replanAttempt": attempt,
            "replanFeedback": feedback or [],
            "ruleHits": rule_hits,
            "textSummary": text_summary,
            "textFacts": deepcopy(delta.get("textFacts", [])),
            "closeStates": [{"id": "S-017-0", "validTo": state["validFrom"]}],
            "createIndividuals": created,
            "assertTypes": assert_types,
            "assertRelations": relations,
            "attachEvidence": evidence,
            "evidenceRecords": delta["evidence"],
            "temporalValues": delta["temporalEntities"],
            "expectedSubgraph": expected,
        }

    @staticmethod
    def _plan_text(delta, ontology, created, assert_types, relations, attachments,
                   expected, hits, main_event, state_type):
        from hashlib import sha256

        facts = delta.get("textFacts", [])
        records = [item for item in delta["evidence"] if item.get("modality") == "text"]
        if not records:
            return {}
        def select(predicate, condition=lambda value: True):
            return [f for f in facts if f["predicate"] == predicate and condition(f["value"])]
        def add_node(node_id, class_id, properties, extra_types=()):
            created.append({"id": node_id, "type": class_id, "properties": properties})
            assert_types.append({"id": node_id, "type": class_id})
            assert_types.extend({"id": node_id, "type": t} for t in extra_types)
            expected.append(node_id)
        def link(s, p, o):
            relation = {"s": s, "p": p, "o": o}
            if relation not in relations:
                relations.append(relation)
        def attach(event, ids):
            for evidence_id in sorted(set(ids)):
                item = {"event": event, "evidence": evidence_id}
                if item not in attachments:
                    attachments.append(item)
                link(event, "hasTextEvidence", evidence_id)
        for record in records:
            source = record["source"]
            class_id = {"AISStatic": "DeclarationStatement", "VesselRegistry": "DeclarationStatement",
                        "EOAnnotation": "InspectionObservation", "DutyLog": "InspectionObservation",
                        "VHFTranscript": "VHFCallEvent", "CommunicationMonitor": "CommunicationEvent"}.get(source, "TextEvidence")
            properties = {"textSource": source, "rawText": record["rawText"],
                          "rawTextSpan": str(record["span"]), "timestamp": record["timestamp"]}
            for fact in facts:
                if fact["evidence"] == record["id"]:
                    properties[fact["predicate"]] = fact["value"]
            add_node(record["id"], class_id, properties, ("TextEvidence",) if class_id in {"VHFCallEvent", "CommunicationEvent"} else ())
            if class_id == "DeclarationStatement":
                link(delta["vesselId"], "declaredAs", record["id"])
            if class_id == "VHFCallEvent":
                link(delta["vesselId"], "calledBy", record["id"])
            if ontology.is_subclass(class_id, "Event"):
                attach(record["id"], [record["id"]])
            if main_event:
                link(main_event, "hasTextEvidence", record["id"])
        for fact in select("foreignAddress"):
            endpoint = "ENDPOINT-" + sha256(fact["value"].encode()).hexdigest()[:12]
            if endpoint not in expected:
                add_node(endpoint, "CommunicationEndpoint", {"address": fact["value"]})
            link(fact["evidence"], "sentTo", endpoint)

        labels, events = [], []
        r4 = ontology.rules.get("R4")
        declarations = select("declaredVesselType", lambda value: value in r4["when"]["declaredTypes"]) if r4 else []
        inspections = select("hasFishingGear", lambda value: value is False) + select("hasDirectionalAntenna", lambda value: value is True)
        if declarations and inspections:
            event = f"{delta['vesselId']}-IDENTITY-{delta['roundId']}"
            support = declarations + inspections
            add_node(event, r4["then"]["eventType"], {"reviewStatus": "pending", "rule": "R4",
                     "confidence": min(f["confidence"] for f in support)})
            attach(event, [f["evidence"] for f in support])
            link(event, "hasSubject", delta["vesselId"])
            for declaration in declarations:
                for inspection in inspections:
                    link(declaration["evidence"], "contradicts", inspection["evidence"])
            hits.append("R4")
            events.append(event)
            labels.append("身份矛盾")
        r5 = ontology.rules.get("R5")
        attempts = select("callAttempts", lambda value: value >= r5["when"]["callAttemptsAtLeast"]) if r5 else []
        no_response = select("responseStatus", lambda value: value == "no_response")
        responded = {f["evidence"] for f in select("responseStatus", lambda value: value == "responded")}
        call_ids = ({f["evidence"] for f in attempts} & {f["evidence"] for f in no_response}) - responded
        calls = [f for f in attempts + no_response if f["evidence"] in call_ids]
        calls = [f for f in calls if f["source"] == "VHFTranscript"]
        encrypted = select("encryptedMessageCount", lambda value: value >= r5["when"]["encryptedMessageCountAtLeast"]) if r5 else []
        if calls and encrypted:
            event = f"{delta['vesselId']}-COMM-{delta['roundId']}"
            support = calls + encrypted
            add_node(event, r5["then"]["eventType"], {"reviewStatus": "pending", "rule": "R5",
                     "confidence": min(f["confidence"] for f in support)})
            attach(event, [f["evidence"] for f in support])
            link(event, "hasSubject", delta["vesselId"])
            hits.append("R5")
            events.append(event)
            labels.append("通信异常")
        r6 = ontology.rules.get("R6")
        reports = select("reportedSuspicion", lambda value: value is True)
        if (r6 and main_event and reports and r6["when"]["requiresRule"] in hits
                and (not r6["when"]["inSensitiveArea"] or delta["areaSensitive"])):
            rule = r6["then"]
            event = next(item for item in created if item["id"] == main_event)
            properties = event["properties"]
            properties["baseConfidence"] = properties["confidence"]
            properties["confidence"] = round(min(rule["confidenceCap"], properties["confidence"] + rule["confidenceBonus"]), 3)
            properties["threatLevel"] = rule["threatLevel"]
            for fact in reports:
                link(main_event, "reportedBy", fact["evidence"])
            hits.append("R6")
        if main_event:
            next(item for item in created if item["id"] == main_event)["properties"].update(labels=labels, ruleHits=list(hits))
        return {"labels": labels, "events": events, "textEvidenceCount": len(records),
                "trajectoryEvidenceCount": len(delta["evidence"]) - len(records)}

    def replan(
        self,
        previous_plan: dict[str, Any],
        validation_report: dict[str, Any],
        delta: dict[str, Any],
        ontology: OntologyModel,
        graph: dict[str, Any],
    ) -> dict[str, Any]:
        # Patch the rejected plan, preserving unrelated decisions and its audit identity.
        plan = deepcopy(previous_plan)
        suggestions = list(validation_report.get("suggestions", []))
        plan["replanAttempt"] = int(previous_plan.get("replanAttempt", 1)) + 1
        plan["replanFeedback"] = suggestions
        repairs = []
        for suggestion in suggestions:
            match = re.fullmatch(r"use a declared class for (.+)", suggestion)
            assertion_match = re.fullmatch(r"declare (.+) and its individual before asserting the type", suggestion)
            targets = {match[1]} if match else set()
            if assertion_match:
                targets.update(item["id"] for item in plan.get("assertTypes", [])
                               if item["type"] == assertion_match[1])
            for individual in plan.get("createIndividuals", []):
                rule_id = individual.get("properties", {}).get("rule", "R3")
                event_type = ontology.rules.get(rule_id, {}).get("then", {}).get("eventType")
                if individual["id"] not in targets or event_type not in ontology.classes:
                    continue
                # Only restore an event justified by the matched rule; never invent a class.
                if rule_id not in plan.get("ruleHits", []) or not ontology.is_subclass(event_type, "Event"):
                    continue
                individual["type"] = event_type
                assertions = plan.setdefault("assertTypes", [])
                found = False
                for assertion in assertions:
                    if assertion["id"] == individual["id"]:
                        assertion["type"] = event_type
                        found = True
                if not found:
                    assertions.append({"id": individual["id"], "type": event_type})
                repairs.append({"suggestion": suggestion, "action": "restoreEventType", "id": individual["id"], "type": event_type})

            if suggestion.startswith("attach "):
                event_ids = {item["id"] for item in plan.get("createIndividuals", [])
                             if ontology.is_subclass(item["type"], "Event")}
                missing = re.fullmatch(r"attach at least one evidence record to (.+)", suggestion)
                if missing:
                    event_ids &= {missing[1]}
                elif suggestion not in {"attach existing evidence to a planned event",
                                        "attach evidence whose subject matches the event subject"}:
                    continue
                evidence = [item for item in delta.get("evidence", [])
                            if item.get("subject") == delta["vesselId"]]
                if not event_ids or not evidence:
                    continue
                planned_events = {item["id"] for item in plan.get("createIndividuals", [])
                                  if ontology.is_subclass(item["type"], "Event")}
                known = {item["id"] for item in evidence}
                links = [item for item in plan.get("attachEvidence", [])
                         if item["event"] in planned_events and item["evidence"] in known]
                for event_id in sorted(event_ids):
                    for item in evidence:
                        link = {"event": event_id, "evidence": item["id"]}
                        if link not in links:
                            links.append(link)
                plan["attachEvidence"] = links
                records = {item["id"]: item for item in plan.get("evidenceRecords", [])}
                records.update({item["id"]: deepcopy(item) for item in evidence})
                plan["evidenceRecords"] = list(records.values())
                repairs.append({"suggestion": suggestion, "action": "restoreEvidence", "events": sorted(event_ids), "evidence": sorted(known)})
        plan["replanRepairs"] = repairs
        return plan


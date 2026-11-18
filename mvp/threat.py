"""Grounded R7-R9 event clustering. Scores are conservative rule scores, not probabilities."""
from __future__ import annotations

import math


CLASSES = {"ThreatAssessment": "Event", "AbnormalTrajectory": "Event",
           "HighRiskBehavior": "Event", "ThreatAlert": "Event"}
RELATIONS = {"hasSubEvent": {"domain": "Event", "range": "Event"},
             "triggersAlert": {"domain": "ThreatAssessment", "range": "ThreatAlert"}}
DATA_PROPERTIES = {"threatLevel": {"domain": "Event", "datatype": "string"},
                   "trajectoryPattern": {"domain": "AbnormalTrajectory", "datatype": "string"},
                   "communicationSilence": {"domain": "CommunicationEvent", "datatype": "string"}}
RULES = {
    "R7": {"id": "R7", "name": "abnormal_trajectory",
           "when": {"patterns": ["circling", "zigzag", "stationary"]},
           "then": {"eventType": "AbnormalTrajectory"},
           "plannerAction": "materialize supplied AIS/Radar trajectory annotations with source evidence"},
    "R8": {"id": "R8", "name": "combined_threat",
           "when": {"requiredEventTypes": ["IdentityConflict", "CommunicationAnomaly", "AbnormalTrajectory"],
                    "inSensitiveArea": True, "minimumConfidence": 0.8},
           "then": {"threatLevel": "high", "eventType": "ThreatAlert"},
           "plannerAction": "aggregate independent anomaly categories; create a local reviewable alert"},
    "R9": {"id": "R9", "name": "high_risk_communication",
           "when": {"callAttemptsAtLeast": 3, "encryptedMessageCountAtLeast": 20},
           "then": {"eventType": "HighRiskBehavior"},
           "plannerAction": "combine explicit radio silence, unanswered VHF calls and frequent encrypted outbound messages"},
}


def perceive_threat(raw, delta):
    enabled = raw.get("enableThreatAssessment", False)
    if not isinstance(enabled, bool):
        raise ValueError("enableThreatAssessment must be a boolean")
    if not enabled:
        return
    delta["enableThreatAssessment"] = True
    facts = []
    known = set()
    # These are upstream annotations, not geometric recognition from sparse points.
    from .text import timestamp
    for item in raw["observations"]:
        if item["id"] in known:
            raise ValueError("duplicate observation ID")
        known.add(item["id"])
        confidence = item["confidence"]
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("observation confidence must be finite and within [0, 1]")
        if item["subject"] != raw["vesselId"]:
            raise ValueError("observation subject does not match round vessel")
        instant = timestamp(item["timestamp"])
        pattern = item.get("trajectoryPattern")
        if pattern is None:
            continue
        if item["source"] not in {"AIS", "Radar"} or pattern not in RULES["R7"]["when"]["patterns"]:
            raise ValueError("unsupported trajectory annotation")
        if timestamp(delta["validFrom"]) <= instant <= timestamp(delta["validTo"]):
            facts.append({"predicate": "trajectoryPattern", "value": pattern,
                          "subject": item["subject"], "evidence": item["id"],
                          "source": item["source"], "timestamp": item["timestamp"],
                          "confidence": confidence})
    delta["trajectoryFacts"] = facts
    for evidence in delta["evidence"]:
        original = next((i for i in raw["observations"] if i["id"] == evidence["id"]), None)
        if original and "trajectoryPattern" in original:
            evidence["trajectoryPattern"] = original["trajectoryPattern"]


def extend_plan(delta, ontology, created, types, relations, attachments, expected, hits):
    if not delta.get("enableThreatAssessment"):
        return {}
    prefix = f"{delta['vesselId']}-{delta['roundId']}"
    records = {e["id"]: e for e in delta["evidence"]}
    def node(suffix, kind, support, properties=None):
        support = sorted(set(support))
        event_id = f"{prefix}-{suffix}"
        props = {"reviewStatus": "pending", "confidence": min(records[i]["confidence"] for i in support)}
        props.update(properties or {})
        created.append({"id": event_id, "type": kind, "properties": props})
        types.append({"id": event_id, "type": kind})
        expected.append(event_id)
        relations.extend([{"s": event_id, "p": "hasSubject", "o": delta["vesselId"]},
                          {"s": event_id, "p": "hasObject", "o": delta["areaId"]},
                          {"s": event_id, "p": "hasInstant", "o": delta["temporalEntities"][-1]["id"]}])
        attachments.extend({"event": event_id, "evidence": i} for i in support)
        return event_id

    trajectory = delta.get("trajectoryFacts", [])
    if trajectory and "R7" in ontology.rules:
        node("TRAJECTORY", "AbnormalTrajectory", [f["evidence"] for f in trajectory],
             {"rule": "R7", "trajectoryPattern": ",".join(sorted({f["value"] for f in trajectory}))})
        hits.append("R7")
    facts = delta.get("textFacts", [])
    def select(predicate, value):
        return [f for f in facts if f["predicate"] == predicate and f["value"] == value]
    silence = select("communicationSilence", "radio_silence")
    outbound = select("outboundEncrypted", True)
    r9 = ontology.rules.get("R9")
    calls = {f["evidence"] for f in facts if f["predicate"] == "callAttempts"
             and r9 and f["value"] >= r9["when"]["callAttemptsAtLeast"]}
    calls &= {f["evidence"] for f in select("responseStatus", "no_response")}
    # An answered call anywhere in the window blocks the radio-silence combination.
    if select("responseStatus", "responded") or select("communicationSilence", "responded"):
        calls.clear()
    comm = {f["evidence"] for f in facts if f["predicate"] == "encryptedMessageCount"
            and r9 and f["value"] >= r9["when"]["encryptedMessageCountAtLeast"]}
    comm &= {f["evidence"] for f in outbound}
    if r9 and silence and calls and comm:
        high_risk = node("HIGH-RISK", "HighRiskBehavior", calls | comm | {f["evidence"] for f in silence}, {"rule": "R9"})
        relations.extend({"s": high_risk, "p": "hasSubEvent", "o": n["id"]}
                         for n in created if n["type"] == "CommunicationAnomaly")
        hits.append("R9")
    categories = {"SuspectedIllegalFishingActivity", "IdentityConflict", "CommunicationAnomaly",
                  "AbnormalTrajectory", "HighRiskBehavior"}
    children = [n for n in created if n["type"] in categories]
    if not children:
        return {}
    child_ids = {n["id"] for n in children}
    support = {a["evidence"] for a in attachments if a["event"] in child_ids}
    # The weakest child sets the cluster score; corroboration bonuses cannot hide weak evidence.
    confidence = min([n["properties"]["confidence"] for n in children]
                     + [records[i]["confidence"] for i in support])
    r8 = ontology.rules.get("R8")
    present = {n["type"] for n in children}
    reasons = []
    if not delta["areaSensitive"]:
        reasons.append("outside_sensitive_area")
    if r8:
        reasons.extend("missing_" + t for t in r8["when"]["requiredEventTypes"] if t not in present)
        if confidence < r8["when"]["minimumConfidence"]:
            reasons.append("insufficient_confidence")
    else:
        reasons.append("R8_not_declared")
    high = not reasons
    level = "high" if high else "elevated" if present & {"CommunicationAnomaly", "AbnormalTrajectory", "HighRiskBehavior"} else "low"
    assessment = node("ASSESSMENT", "ThreatAssessment", support,
                      {"confidence": confidence, "threatLevel": level,
                       "confidenceMethod": "minimum_child_and_evidence_confidence", "blockedReasons": reasons})
    relations.extend({"s": assessment, "p": "hasSubEvent", "o": n["id"]} for n in children)
    alert = None
    if high:
        hits.append("R8")
        alert = node("ALERT", "ThreatAlert", support, {"rule": "R8", "threatLevel": "high",
                     "confidence": confidence, "delivery": "local_graph_only"})
        relations.append({"s": assessment, "p": "triggersAlert", "o": alert})
    return {"assessmentId": assessment, "subEvents": sorted(child_ids), "threatLevel": level,
            "confidence": confidence, "confidenceMethod": "minimum_child_and_evidence_confidence",
            "alertId": alert, "blockedReasons": reasons,
            "trajectoryPatterns": sorted({f["value"] for f in trajectory})}


def validate_threat_plan(plan, delta, ontology):
    """Re-derive conclusions from perception; reject removed, invented or altered alerts."""
    if not delta.get("enableThreatAssessment"):
        return True
    from .agents import DecisionPlanningAgent
    reference = DecisionPlanningAgent().plan(delta, ontology, {})
    kinds = set(CLASSES) | {"SuspectedIllegalFishingActivity", "IdentityConflict", "CommunicationAnomaly"}
    def view(p):
        nodes = [n for n in p["createIndividuals"] if n["type"] in kinds]
        ids = {n["id"] for n in reference["createIndividuals"] if n["type"] in kinds}
        ids.update(n["id"] for n in nodes)
        return {"nodes": nodes, "summary": p.get("threatSummary"),
                "relations": [r for r in p["assertRelations"] if r["s"] in ids],
                "attachments": [a for a in p["attachEvidence"] if a["event"] in ids],
                "rules": [r for r in p["ruleHits"] if r in RULES]}
    return view(plan) == view(reference)

"""Grounded text extraction; all offsets are Python Unicode [start, end)."""
from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("text timestamps must include a timezone")
    return result


class LLMTextExtractor(ABC):
    """Optional adapter. Return candidates: predicate, value, span; no network by default.

    Candidates remain separate from rule facts until reviewed by an application.
    Source IDs, subjects, confidence and quoted text are assigned by the caller.
    """

    @abstractmethod
    def extract(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError


class TextPerceptionAgent:
    name = "text_perception_agent"
    sources = {"AISStatic", "VHFTranscript", "DutyLog", "VesselRegistry",
               "EOAnnotation", "CommunicationMonitor", "PublicReport"}
    predicates = {"vesselName", "callSign", "declaredVesselType", "declaredGearType",
                  "hasFishingGear", "hasDirectionalAntenna", "callAttempts", "responseStatus",
                  "encryptedMessageCount", "foreignAddress", "reportedSuspicion"}

    def __init__(self, llm_extractor: LLMTextExtractor | None = None):
        self.llm_extractor = llm_extractor

    def perceive(self, records: list[dict[str, Any]], vessel_id: str, start: str, end: str,
                 tolerance_seconds: int = 120) -> dict[str, Any]:
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError("texts must be a list of record objects")
        if isinstance(tolerance_seconds, bool) or not isinstance(tolerance_seconds, int) or tolerance_seconds < 0:
            raise ValueError("text tolerance must be a nonnegative integer number of seconds")
        lower = timestamp(start) - timedelta(seconds=tolerance_seconds)
        upper = timestamp(end) + timedelta(seconds=tolerance_seconds)
        evidence, facts, candidates, excluded = [], [], [], []
        ids = set()
        for original in records:
            record = deepcopy(original)
            for field in ("id", "source", "subject", "timestamp", "rawText"):
                if not isinstance(record.get(field), str) or not record[field]:
                    raise ValueError(f"text record requires nonempty {field}")
            if record["id"] in ids:
                raise ValueError(f"duplicate text evidence ID: {record['id']}")
            ids.add(record["id"])
            if record["source"] not in self.sources:
                raise ValueError(f"unsupported text source: {record['source']}")
            confidence = record.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("text confidence must be finite and in [0, 1]")
            self._span(record["rawText"], record.get("span"))
            instant = timestamp(record["timestamp"])
            reason = "subject_mismatch" if record["subject"] != vessel_id else "outside_window" if not lower <= instant <= upper else None
            if reason:
                excluded.append({"id": record["id"], "reason": reason})
                continue
            record.update(type="TextEvidence", modality="text")
            evidence.append(record)
            facts.extend(self._extract(record))
            if self.llm_extractor is not None:
                for candidate in self.llm_extractor.extract(deepcopy(record)):
                    if candidate.get("predicate") not in self.predicates or "value" not in candidate:
                        raise ValueError("invalid LLM text fact")
                    quote = self._span(record["rawText"], candidate.get("span"))
                    a, b = candidate["span"]
                    if not record["span"][0] <= a < b <= record["span"][1]:
                        raise ValueError("LLM span outside evidence span")
                    candidates.append({"predicate": candidate["predicate"], "value": candidate["value"],
                                       "subject": vessel_id, "source": record["source"],
                                       "confidence": confidence, "evidence": record["id"],
                                       "span": [a, b], "quote": quote, "extractor": "llm", "status": "candidate"})
        return {"textFacts": facts, "textCandidates": candidates, "evidence": evidence,
                "textWindow": {"from": lower.isoformat(), "to": upper.isoformat(), "toleranceSeconds": tolerance_seconds},
                "excludedTexts": excluded}

    @staticmethod
    def _span(text: str, span: Any) -> str:
        if (not isinstance(span, list) or len(span) != 2 or any(type(i) is not int for i in span)
                or not 0 <= span[0] < span[1] <= len(text)):
            raise ValueError("span must be Unicode character offsets [start, end) within rawText")
        return text[span[0]:span[1]]

    def _extract(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        text = self._span(record["rawText"], record["span"])
        offset = record["span"][0]
        result = []
        patterns = [
            ("vesselName", r"船名[：:]\s*([^，。；;\s]+)", lambda m: m[1]),
            ("callSign", r"呼号[：:]\s*([A-Za-z0-9-]+)", lambda m: m[1]),
            ("declaredVesselType", r"(?:申报船型[：:]|登记船型[：:]|申报为)\s*(捕捞船|渔船|货船|客船)", lambda m: m[1]),
            ("declaredGearType", r"申报渔具[：:]\s*([^，。；;\s]+)", lambda m: m[1]),
            ("hasFishingGear", r"(?:无渔具|没有渔具|未发现渔具)", lambda m: False),
            ("hasFishingGear", r"(?:有渔具|发现渔具)", lambda m: True),
            ("hasDirectionalAntenna", r"(?:发现|安装|装有|有)定向(?:接收)?天线", lambda m: True),
            ("callAttempts", r"呼叫\s*(\d+)\s*次", lambda m: int(m[1])),
            ("responseStatus", r"(?:无应答|未应答)", lambda m: "no_response"),
            ("responseStatus", r"(?:应答状态[：:]\s*已应答|正常应答)", lambda m: "responded"),
            ("encryptedMessageCount", r"(?:加密短报文|加密报文)\s*(\d+)\s*(?:条|次)", lambda m: int(m[1])),
            ("foreignAddress", r"境外地址[：:]\s*([A-Za-z0-9.:-]+)", lambda m: m[1]),
            ("reportedSuspicion", r"(?:举报|报告)[：:]\s*疑似(?:非法捕捞|违规捕捞)", lambda m: True),
        ]
        allowed = {
            "AISStatic": {"vesselName", "callSign", "declaredVesselType", "declaredGearType"},
            "VesselRegistry": {"vesselName", "callSign", "declaredVesselType", "declaredGearType"},
            "EOAnnotation": {"hasFishingGear", "hasDirectionalAntenna"},
            "DutyLog": {"hasFishingGear", "hasDirectionalAntenna", "callAttempts", "responseStatus"},
            "VHFTranscript": {"callAttempts", "responseStatus", "callSign"},
            "CommunicationMonitor": {"encryptedMessageCount", "foreignAddress"},
            "PublicReport": {"reportedSuspicion"},
        }[record["source"]]
        for predicate, pattern, value in patterns:
            if predicate not in allowed:
                continue
            for match in re.finditer(pattern, text):
                # Avoid extracting a positive fragment from a directly negated statement.
                if re.search(r"(?:未|没|没有|并非|不是|否认|并未|未发现|没有发现|未见)$", text[max(0, match.start()-6):match.start()]):
                    continue
                a, b = offset + match.start(), offset + match.end()
                result.append({"subject": record["subject"], "predicate": predicate, "value": value(match),
                               "evidence": record["id"], "span": [a, b], "quote": record["rawText"][a:b],
                               "source": record["source"], "confidence": record["confidence"], "extractor": "regex"})
        return result


TEXT_CLASSES = {
    "TextEvidence": "TemporalEntity", "DeclarationStatement": "TextEvidence",
    "VHFCallEvent": "Event", "CommunicationEvent": "Event", "InspectionObservation": "TextEvidence",
    "IdentityConflict": "Event", "CommunicationAnomaly": "Event", "CommunicationEndpoint": "TemporalEntity",
}
TEXT_RELATIONS = {
    "hasTextEvidence": {"domain": "Event", "range": "TextEvidence"},
    "declaredAs": {"domain": "Plat", "range": "DeclarationStatement"},
    "calledBy": {"domain": "Plat", "range": "VHFCallEvent"},
    "sentTo": {"domain": "CommunicationEvent", "range": "CommunicationEndpoint"},
    "contradicts": {"domain": "DeclarationStatement", "range": "InspectionObservation"},
    "reportedBy": {"domain": "Event", "range": "TextEvidence"},
}
TEXT_DATA_PROPERTIES = {
    "callSign": {"domain": "TextEvidence", "datatype": "string"},
    "declaredVesselType": {"domain": "DeclarationStatement", "datatype": "string"},
    "declaredGearType": {"domain": "DeclarationStatement", "datatype": "string"},
    "callAttempts": {"domain": "VHFCallEvent", "datatype": "integer"},
    "responseStatus": {"domain": "VHFCallEvent", "datatype": "string"},
    "encryptedMessageCount": {"domain": "CommunicationEvent", "datatype": "integer"},
    "textSource": {"domain": "TextEvidence", "datatype": "string"},
    "rawTextSpan": {"domain": "TextEvidence", "datatype": "string"},
}
TEXT_RULES = {
    "R4": {"id": "R4", "name": "identity_conflict", "when": {"declaredTypes": ["捕捞船", "渔船"]},
           "then": {"eventType": "IdentityConflict"}, "plannerAction": "materialize identity conflict from declaration and inspection evidence"},
    "R5": {"id": "R5", "name": "communication_anomaly", "when": {"callAttemptsAtLeast": 3, "encryptedMessageCountAtLeast": 20},
           "then": {"eventType": "CommunicationAnomaly"}, "plannerAction": "materialize anomaly from unanswered calls and encrypted message count in round window"},
    "R6": {"id": "R6", "name": "report_corroboration", "when": {"requiresRule": "R1", "inSensitiveArea": True},
           "then": {"confidenceBonus": 0.05, "confidenceCap": 0.99, "threatLevel": "elevated"},
           "plannerAction": "raise candidate confidence and threat level using report, loitering and sensitive area"},
}

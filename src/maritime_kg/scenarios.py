"""Reusable learning scenarios for the prototype software."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import ObservationEvent
from .sample_data import build_sample_observations


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    learning_goal: str


SCENARIOS = (
    Scenario("full_update", "完整更新闭环", "正常船与异常船同时进入系统，包含 AIS、雷达和未知通信字段。", "观察候选事实、图谱提交和本体演化建议。"),
    Scenario("normal_only", "正常航行", "目标持续高速航行，不满足悬停规则。", "理解为什么系统不会产生误报。"),
    Scenario("late_data", "迟到与乱序数据", "同一批观测按到达顺序打乱，但按观测时间重新判断。", "理解观测时间和接收时间的区别。"),
    Scenario("single_modality", "单模态不足", "只提供 AIS，不满足双模态证据要求。", "理解跨模态证据门槛。"),
    Scenario("schema_drift", "新字段演化", "加入新的加密通信频次字段，触发本体演化建议。", "理解本体 v0.1 如何发现未知字段。"),
)


def scenario_catalog() -> list[dict[str, str]]:
    return [scenario.__dict__ for scenario in SCENARIOS]


def build_scenario(key: str) -> list[ObservationEvent]:
    events = build_sample_observations()
    if key == "full_update" or key == "schema_drift":
        return events
    if key == "normal_only":
        return [event for event in events if "412345678" in event.event_id]
    if key == "single_modality":
        return [event for event in events if event.modality.value == "ais"]
    if key == "late_data":
        return list(reversed(events))
    raise KeyError(f"unknown scenario: {key}")


def write_scenario(key: str, path: Path) -> list[ObservationEvent]:
    events = build_scenario(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8")
    return events

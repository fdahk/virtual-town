"""
Agent 日程模型。

每个 NPC 的 `schedule_template` 是一个按天重复的片段数组：
```json
[
  {"start": "07:00", "end": "08:00", "activity": "breakfast", "location_id": "loc_xiaofang_home"},
  {"start": "08:00", "end": "12:00", "activity": "work_at_cafe", "location_id": "loc_hobbs_cafe"}
]
```

规则版 Agent 根据当前游戏世界时间选择合适的片段作为当前目标。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    h = int(hh)
    # "24:00" is a conventional way to denote end-of-day midnight in schedule
    # data. Python's time() rejects hour=24, so normalise to 00:00 (midnight).
    # pick_current_slot's cross-midnight branch handles start > end correctly.
    if h == 24:
        return time(0, 0)
    return time(h, int(mm))


@dataclass(slots=True)
class ScheduleSlot:
    start: time
    end: time
    activity: str
    location_id: str | None
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleSlot":
        return cls(
            start=_parse_time(data["start"]),
            end=_parse_time(data["end"]),
            activity=data.get("activity", "idle"),
            location_id=data.get("location_id"),
            description=data.get("description", data.get("activity", "")),
        )


def pick_current_slot(
    template: list[dict[str, Any]], current: time
) -> ScheduleSlot | None:
    """选择当前时间所处的日程片段；没有则返回 None。"""
    for item in template:
        slot = ScheduleSlot.from_dict(item)
        if slot.start <= slot.end:
            if slot.start <= current < slot.end:
                return slot
        else:
            # 跨夜情况（例：22:00-06:00）
            if current >= slot.start or current < slot.end:
                return slot
    return None

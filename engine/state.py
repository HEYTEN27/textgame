"""Состояние игрока: локация, инвентарь, флаги, статы, журнал."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GameState:
    scenario_id: str
    node: str
    inventory: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    stats: dict[str, int] = field(default_factory=dict)
    visited: dict[str, int] = field(default_factory=dict)
    journal: list[str] = field(default_factory=list)
    turns: int = 0
    timers: dict[str, dict[str, Any]] = field(default_factory=dict)
    used_once: set[str] = field(default_factory=set)
    input_attempts: dict[str, int] = field(default_factory=dict)

    # --- инвентарь ---
    def has(self, item_id: str) -> bool:
        return item_id in self.inventory

    def give(self, item_id: str) -> bool:
        if item_id in self.inventory:
            return False
        self.inventory.append(item_id)
        return True

    def take(self, item_id: str) -> bool:
        if item_id not in self.inventory:
            return False
        self.inventory.remove(item_id)
        return True

    # --- таймеры ---
    def timer(self, timer_id: str) -> dict[str, Any]:
        return self.timers.setdefault(
            timer_id, {"ticks": 0, "fires": 0, "done": False, "pending_goto": False}
        )

    # --- сериализация ---
    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "node": self.node,
            "inventory": list(self.inventory),
            "flags": sorted(self.flags),
            "stats": dict(self.stats),
            "visited": dict(self.visited),
            "journal": list(self.journal),
            "turns": self.turns,
            "timers": {k: dict(v) for k, v in self.timers.items()},
            "used_once": sorted(self.used_once),
            "input_attempts": dict(self.input_attempts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        if not isinstance(data, dict):
            raise ValueError("состояние должно быть объектом")
        try:
            st = cls(
                scenario_id=str(data["scenario_id"]),
                node=str(data["node"]),
                inventory=[str(x) for x in data.get("inventory", [])],
                flags={str(x) for x in data.get("flags", [])},
                stats={str(k): int(v) for k, v in data.get("stats", {}).items()},
                visited={str(k): int(v) for k, v in data.get("visited", {}).items()},
                journal=[str(x) for x in data.get("journal", [])],
                turns=int(data.get("turns", 0)),
                timers={
                    str(k): {
                        "ticks": int(v.get("ticks", 0)),
                        "fires": int(v.get("fires", 0)),
                        "done": bool(v.get("done", False)),
                        "pending_goto": bool(v.get("pending_goto", False)),
                    }
                    for k, v in data.get("timers", {}).items()
                },
                used_once={str(x) for x in data.get("used_once", [])},
                input_attempts={
                    str(k): int(v) for k, v in data.get("input_attempts", {}).items()
                },
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"повреждённое состояние: {exc}") from exc
        return st

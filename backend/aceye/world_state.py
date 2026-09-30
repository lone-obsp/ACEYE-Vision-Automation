"""Structured state contracts for future Minecraft/long-horizon agents."""

from __future__ import annotations

import copy
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorldState:
    scene: dict[str, Any] = field(default_factory=dict)
    objects: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    ui: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, Any] = field(default_factory=dict)
    player_state: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    goal: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)

    def update_from_vision(self, vision: dict[str, Any], *, event: dict[str, Any] | None = None) -> None:
        self.objects = copy.deepcopy(vision.get("objects", self.objects))
        self.ui = {"texts": copy.deepcopy(vision.get("texts", []))}
        if event:
            self.events.append(copy.deepcopy(event))
            self.events = self.events[-100:]
        self.updated_at = time.monotonic()
        self.history.append({"time": self.updated_at, "objects": copy.deepcopy(self.objects), "events": copy.deepcopy(self.events[-5:])})
        self.history = self.history[-200:]

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in self.__dict__.items() if key != "updated_at"} | {"updated_at": self.updated_at})


class EpisodicMemory:
    def __init__(self, maxlen: int = 200) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def append(self, item: dict[str, Any]) -> None:
        self._items.append(copy.deepcopy(item))

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._items)[-max(0, int(limit)):]


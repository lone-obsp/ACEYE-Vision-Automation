"""Async image-generation contracts; no generator is loaded in realtime paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ImageGenerationRequest:
    source_frame: Any = None
    world_state: dict[str, Any] | None = None
    prompt: str = ""
    mode: str = "creative"
    seed: int | None = None
    proposed_action: dict[str, Any] | None = None


@dataclass(slots=True)
class ImageGenerationResult:
    image: Any = None
    latency_ms: float = 0.0
    backend: str = "none"
    metadata: dict[str, Any] | None = None


class GenerativeVisionBackend(Protocol):
    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


class DisabledGenerativeBackend:
    """Safe placeholder until a local Diffusers/ComfyUI backend is selected."""

    name = "disabled"

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        return ImageGenerationResult(backend=self.name, metadata={"status": "disabled", "mode": request.mode})


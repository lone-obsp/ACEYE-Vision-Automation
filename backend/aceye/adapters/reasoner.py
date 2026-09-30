"""Qwen3-VL local Transformers and OpenAI-compatible HTTP reasoners."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .common import AdapterError, empty_result, extract_json, jsonable, parse_actions, require_numpy, resolve_local_path


class Qwen3VlReasonerAdapter:
    name = "reasoner"

    def __init__(self, config: dict[str, Any] | None = None, root: Path | None = None) -> None:
        self.config = dict(config or {})
        self.root = Path(root or ".").resolve()
        self.provider = str(self.config.get("provider", self.config.get("backend", "local"))).lower()
        self._processor: Any = None
        self._model: Any = None

    def load(self) -> "Qwen3VlReasonerAdapter":
        if self.provider in {"http", "endpoint", "openai", "openai_compatible"}:
            endpoint = self.config.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
                raise AdapterError(self.name, "HTTP reasoner endpoint is missing or invalid", hint="Set provider=http and endpoint=http://127.0.0.1:1234/v1/chat/completions")
            if not self.config.get("model"):
                raise AdapterError(self.name, "HTTP reasoner model is missing", hint="Set the model name accepted by the local server")
            return self
        if self.provider not in {"local", "transformers", "qwen3-vl"}:
            raise AdapterError(self.name, f"unsupported provider: {self.provider}", hint="Use local or http")
        model_dir = resolve_local_path(
            self.root,
            self.config.get("model_dir", self.config.get("model", "models/Qwen3-VL-2B-Instruct")),
            setting=self.name,
        )
        try:
            import torch
            from transformers import AutoProcessor
        except ImportError as exc:
            raise AdapterError(self.name, "torch and transformers are required for the local reasoner", hint="Install requirements-models.txt") from exc
        model_cls = None
        import_error: Exception | None = None
        try:
            from transformers import AutoModelForImageTextToText as model_cls
        except ImportError as exc:
            import_error = exc
            try:
                from transformers import AutoModelForVision2Seq as model_cls
            except ImportError:
                try:
                    from transformers import AutoModelForCausalLM as model_cls
                except ImportError as final_exc:
                    raise AdapterError(self.name, "installed transformers has no multimodal model loader", hint="Upgrade transformers to the version required by Qwen3-VL") from final_exc
        try:
            self._processor = AutoProcessor.from_pretrained(str(model_dir), local_files_only=True)
            kwargs: dict[str, Any] = {
                "local_files_only": True,
                "device_map": self.config.get("device_map", "auto"),
                "trust_remote_code": bool(self.config.get("trust_remote_code", False)),
            }
            if bool(self.config.get("load_in_4bit", True)):
                try:
                    from transformers import BitsAndBytesConfig
                    kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
                except ImportError as exc:
                    raise AdapterError(self.name, "4-bit mode requires transformers BitsAndBytesConfig and bitsandbytes", hint="Install bitsandbytes or set load_in_4bit=false") from exc
            self._model = model_cls.from_pretrained(str(model_dir), **kwargs)
            self._model.eval()
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(self.name, f"failed to load local Qwen3-VL model {model_dir}: {exc}") from exc
        return self

    def describe(self) -> str:
        if self.provider in {"http", "endpoint", "openai", "openai_compatible"}:
            return f"OpenAI-compatible reasoner ({self.config.get('model', 'unset')})"
        return f"Qwen3-VL local Transformers ({self.config.get('model_dir', self.config.get('model', 'unloaded'))})"

    def infer(self, frame_bgr: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        prompt = str(context.get("prompt") or self._build_prompt(context))
        if self.provider in {"http", "openai", "openai_compatible"}:
            text = self._infer_http(frame_bgr, prompt)
        else:
            text = self._infer_local(frame_bgr, prompt)
        parsed = extract_json(text) or {}
        return {
            **empty_result(),
            "reasoning": {"text": text, "actions": parse_actions(parsed.get("actions"))},
        }

    @staticmethod
    def _build_prompt(context: dict[str, Any]) -> str:
        vision = context.get("vision", context)
        return (
            "根据当前屏幕视觉结果和 OCR 结果判断场景。只输出 JSON，格式为 "
            '{"scene":"","event":"","risk":"","actions":[]}。actions 中只允许 mouse_move, '
            "mouse_button_down, mouse_button_up, key_down, key_up, text, wait。\n"
            + json.dumps(jsonable(vision), ensure_ascii=False)
        )

    def _infer_local(self, frame_bgr: Any, prompt: str) -> str:
        if self._model is None or self._processor is None:
            raise AdapterError(self.name, "local reasoner is not loaded", hint="Call adapter.load()")
        if frame_bgr is None:
            raise AdapterError(self.name, "local Qwen3-VL requires a screen frame")
        require_numpy(self.name)
        try:
            from PIL import Image
            import torch
        except ImportError as exc:
            raise AdapterError(self.name, "Pillow and torch are required for local image reasoning", hint="Install requirements-models.txt") from exc
        np = require_numpy(self.name)
        image = Image.fromarray(np.asarray(frame_bgr)[:, :, ::-1])
        max_side = int(self.config.get("image_max_side", 768))
        image.thumbnail((max_side, max_side))
        messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
        try:
            text_input = self._processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self._processor(text=[text_input], images=[image], return_tensors="pt", padding=True)
            device = getattr(self._model, "device", None)
            if device is not None:
                inputs = {key: value.to(device) if hasattr(value, "to") else value for key, value in inputs.items()}
            with torch.inference_mode():
                generated = self._model.generate(**inputs, max_new_tokens=int(self.config.get("max_new_tokens", 192)))
            input_length = inputs["input_ids"].shape[-1] if "input_ids" in inputs else 0
            generated = generated[:, input_length:] if input_length else generated
            return self._processor.batch_decode(generated, skip_special_tokens=True)[0]
        except Exception as exc:
            raise AdapterError(self.name, f"Qwen3-VL inference failed: {exc}") from exc

    def _infer_http(self, frame_bgr: Any, prompt: str) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if frame_bgr is not None and bool(self.config.get("include_image", False)):
            np = require_numpy(self.name)
            try:
                from PIL import Image
                image = Image.fromarray(np.asarray(frame_bgr)[:, :, ::-1])
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=int(self.config.get("jpeg_quality", 80)))
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            except ImportError as exc:
                raise AdapterError(self.name, "Pillow is required when HTTP image input is enabled", hint="Install requirements-models.txt") from exc
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        payload = {
            "model": self.config.get("model"),
            "messages": [{"role": "user", "content": content}],
            "temperature": float(self.config.get("temperature", 0.1)),
            "max_tokens": int(self.config.get("max_new_tokens", 192)),
        }
        request = urllib.request.Request(
            str(self.config["endpoint"]),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **(self.config.get("headers") or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=float(self.config.get("timeout_seconds", 20))) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AdapterError(self.name, f"HTTP reasoner request failed: {exc}") from exc
        try:
            content_value = body["choices"][0]["message"]["content"]
            if isinstance(content_value, list):
                return "".join(str(part.get("text", "")) for part in content_value if isinstance(part, dict))
            return str(content_value)
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterError(self.name, "HTTP reasoner response has no choices[0].message.content") from exc

    def close(self) -> None:
        self._processor = None
        self._model = None

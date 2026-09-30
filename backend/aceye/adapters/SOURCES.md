# Model adapter sources

The adapters are thin wrappers around the upstream APIs.  We pin no weights
and never download a model at runtime; put the exact files/directories in the
configured local `models/` tree.

- Ultralytics tracking and ByteTrack: <https://docs.ultralytics.com/modes/track>
- Ultralytics SAM 2.1 model names and prompt inference: <https://docs.ultralytics.com/models/sam-2>
- PaddleOCR 3.x `predict`, `rec_texts`, `rec_scores`, and `rec_boxes`: <https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.md>
- Qwen3-VL local Transformers loading: <https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct>
- OpenAI-compatible chat completions format: <https://platform.openai.com/docs/api-reference/chat>

## Local files expected by the default configuration

```text
models/
├── yolo26n-seg.pt
├── sam2.1_t.pt
├── ocr/
│   ├── det/                 # PaddleOCR 3.x text detection model directory
│   └── rec/                 # PaddleOCR 3.x text recognition model directory
└── Qwen3-VL-2B-Instruct/   # complete Hugging Face snapshot
```

The detector uses `model.track(..., persist=True, tracker="bytetrack.yaml")`.
`persist=True` is intentionally scoped to the one screen stream so IDs are not
reused across unrelated input sources. SAM receives detector boxes through the
`context["objects"]` contract. The reasoner emits proposed actions only; the
backend's input sink must still enforce its automation policy.


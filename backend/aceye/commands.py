from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    args: list[str]
    raw: str


def parse_command(raw: str) -> Command:
    raw = raw.strip()
    if not raw:
        return Command("", [], raw)
    try:
        parts = shlex.split(raw, posix=False)
    except ValueError as exc:
        raise ValueError(f"命令引号不完整: {exc}") from exc
    if parts and parts[0].startswith("/"):
        parts[0] = parts[0][1:]
    return Command(parts[0].lower(), parts[1:], raw)


HELP = """可用指令：
/start        启动捕获和模型流水线
/stop         停止流水线
/fps 30       设置捕获目标帧率
/conf 0.4     设置检测置信度
/ocr on|off   开关 OCR
/overlay on|off 开关 Overlay 输出
/goal 文本     修改 VLM/LLM 目标
/reason       立即请求一次理解
/sam          对当前目标请求精细分割
/dryrun on|off 输入动作仅记录或允许发送
/status       查看当前状态
/emergency    立即停止流水线并关闭自动化
/help         显示帮助
"""

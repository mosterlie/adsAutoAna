# -*- coding: utf-8 -*-
"""WorkBuddy 大模型调用的公共入口 (透传模式)

模型名与客户端统一由 backend/wb_config.py 解析/构造:
  - text   : 文本判断(相关性 / 标题同类 / 词级结论)
  - vision : 图片判断(主图相似度)
运行时可改: 「配置管理」页与「配置」弹窗写的是同一个值。
"""
import json
import re
from typing import Any, Dict, Optional

import config
from backend import wb_config


def model_name(kind: str = "text") -> str:
    from backend.relevance import current_model
    return current_model("vision" if kind == "vision" else "workbuddy")


def client(model: str, timeout: Optional[float] = None):
    """取透传客户端(统一入口在 wb_config)"""
    return wb_config.client(model, "vision" if "v" in model else "text",
                            timeout_sec=timeout)


def ask(prompt: str, model: Optional[str] = None, kind: str = "text",
        max_tokens: Optional[int] = None) -> str:
    """单轮对话, 返回文本"""
    m = model or model_name(kind)
    mt = max_tokens or (config.WB_VISION_MAX_TOKENS if kind == "vision"
                        else config.WB_MAX_TOKENS)
    r = client(m).chat_raw(prompt, temperature=config.WB_TEMPERATURE,
                           max_tokens=mt)
    return (r.content or "").strip()


def ask_json(prompt: str, model: Optional[str] = None, kind: str = "text",
             max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """要求模型输出 JSON, 宽松解析"""
    raw = ask(prompt, model, kind, max_tokens)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise RuntimeError(f"模型未返回 JSON: {raw[:120]}")
    return json.loads(m.group(0))


def vision_messages(prompt: str, b64_list: list) -> Dict[str, Any]:
    """组装视觉请求体 (prompt + 若干 base64 图)"""
    content = [{"type": "text", "text": prompt}]
    for b in b64_list:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b}})
    return {"messages": [{"role": "user", "content": content}]}

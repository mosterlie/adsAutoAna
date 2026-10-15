"""WorkBuddy / CodeBuddy 模型 API 客户端（仅标准库实现）。

- 上游：``https://copilot.tencent.com/v2/chat/completions``
- 鉴权：``Authorization: Bearer ck_xxx``（同时下发 ``X-API-Key``）
- 上游仅接受流式请求，非流式（``stream: false``）会返回业务码 11101；
  本客户端始终以流式发起，需要完整结果时在本地聚合。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union

from .config import (
    MODE_NORMAL,
    MODE_PASSTHROUGH,
    Config,
    load_config,
    normalize_mode,
)

#: normal 模式默认注入的系统提示词，可用 ``system_prompt`` 覆盖
DEFAULT_SYSTEM_PROMPT = "\n".join(
    [
        "你是 WorkBuddy，一个由腾讯打造的 AI 助手。",
        "请用简体中文回答，回答保持准确、简洁、可执行。",
        "涉及代码时给出可直接运行的完整示例，并说明关键参数。",
    ]
)

#: 除协议必需字段外，仅在调用方显式传入时才下发的字段
_PASSTHROUGH_FIELDS = (
    "tools",
    "tool_choice",
    "temperature",
    "top_p",
    "max_tokens",
    "reasoning_effort",
)

#: 本 Key 实测可用的模型 ID（2026-10-08 实测；实际可用性以账号权限为准）
KNOWN_MODELS: tuple[str, ...] = (
    "auto",
    "hy4-preview",
    "hy3",
    "hy3-preview",
    "hy3-preview-agent",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v4.1-flash",
    "glm-5.3",
    "glm-5.2",
    "glm-5.1",
    "glm-5v-turbo",
    "kimi-k3",
    "kimi-k2.7",
    "kimi-k2.6",
    "minimax-m3",
    "minimax-m3-pay",
)

#: 实测「仅授权用户可用」（11102）的模型 ID
RESTRICTED_MODELS: tuple[str, ...] = ("gpt-5.6-luna", "gemini-3.1-pro")

MessagesInput = Union[str, Sequence[Mapping[str, Any]], Mapping[str, Any]]


class WorkBuddyError(Exception):
    """上游返回的业务错误（形如 ``{"code": 11101, "msg": "...", "requestId": "..."}``）。"""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        status: Optional[int] = None,
        request_id: Optional[str] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.request_id = request_id
        self.body = body

    def __str__(self) -> str:  # pragma: no cover - 便于排查
        parts = [self.message]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.request_id:
            parts.append(f"requestId={self.request_id}")
        return " | ".join(parts)


@dataclass
class StreamEvent:
    """流式事件：``start`` / ``meta`` / ``reasoning`` / ``text`` / ``tool_calls`` / ``done``。"""

    type: str
    text: str = ""
    mode: Optional[str] = None
    id: Optional[str] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ChatResult:
    """聚合后的完整结果。"""

    id: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    content: str = ""
    reasoning_content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

    def __str__(self) -> str:  # pragma: no cover
        return self.content


def _to_messages(prompt: MessagesInput) -> List[Dict[str, Any]]:
    if isinstance(prompt, str):
        return [{"role": "user", "content": prompt}]
    if isinstance(prompt, Mapping):
        messages = prompt.get("messages")
        if isinstance(messages, Sequence):
            return [dict(item) for item in messages]
        raise TypeError("映射输入必须包含 messages 列表")
    if isinstance(prompt, Sequence):
        return [dict(item) for item in prompt]
    raise TypeError("输入应为字符串、消息列表，或包含 messages 的映射")


def _to_error(status: Optional[int], text: str) -> WorkBuddyError:
    payload: Any = None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None

    if isinstance(payload, Mapping):
        message = payload.get("msg") or payload.get("message") or text
        code = payload.get("code")
        request_id = payload.get("requestId")
    else:
        message = (text or f"上游返回 HTTP {status}")[:500]
        code = None
        request_id = None

    return WorkBuddyError(
        str(message),
        code=code if isinstance(code, int) else None,
        status=status,
        request_id=request_id if isinstance(request_id, str) else None,
        body=payload if payload is not None else text,
    )


def _render_content(content: Any) -> List[str]:
    if content is None:
        return ["(空)"]
    if isinstance(content, str):
        return content.splitlines() or [""]
    return json.dumps(content, ensure_ascii=False, indent=2).splitlines()


def format_payload(
    body: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    """把最终发送给基座模型的请求体渲染成可读文本。"""
    messages = list(body.get("messages") or [])
    rows: List[tuple[str, Any]] = [("mode", mode or "-")]
    for key, value in body.items():
        if key == "messages":
            continue
        if isinstance(value, (list, tuple)):
            shown: Any = f"{len(value)} 项"
        elif isinstance(value, Mapping):
            shown = json.dumps(value, ensure_ascii=False)
        else:
            shown = value
        rows.append((key, shown))  # 如实展示所有下发给基座模型的字段
    rows.append(("messages", f"共 {len(messages)} 条"))

    lines: List[str] = []
    if title:
        lines.append(f"========== {title} ==========")
    width = max(len(key) for key, _ in rows)
    lines.extend(f"{key:<{width}} : {value}" for key, value in rows)
    for index, message in enumerate(messages, 1):
        lines.append("")
        lines.append(f"[{index}] role={message.get('role')}")
        if message.get("name"):
            lines.append(f"    name={message['name']}")
        if message.get("tool_call_id"):
            lines.append(f"    tool_call_id={message['tool_call_id']}")
        lines.extend(f"    {line}" for line in _render_content(message.get("content")))
        if message.get("tool_calls"):
            lines.append(
                f"    tool_calls: {json.dumps(message['tool_calls'], ensure_ascii=False)}"
            )

    lines.append("")
    lines.append("=" * 46)
    return "\n".join(lines)


def print_payload(
    body: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
    title: Optional[str] = None,
    file: Any = None,
) -> None:
    """打印最终发送给基座模型的提示词（默认输出到 stderr，避免与流式正文混杂）。"""
    print(format_payload(body, mode=mode, title=title), file=file or sys.stderr)


class WorkBuddyClient:
    """WorkBuddy 模型客户端，支持「正常调用」与「提示词透传」两种模式。

    >>> client = WorkBuddyClient()                       # 读取 WORKBUDDY_API_KEY
    >>> client.chat("你好").content                       # 模式 1：正常调用
    >>> client.chat_raw("你好").content                   # 模式 2：提示词透传
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        mode: Optional[str] = None,
        system_prompt: Optional[str] = None,
        timeout: Optional[float] = None,
        env_file: Optional[str] = None,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        config: Config = load_config(
            env_file=env_file,
            api_key=api_key,
            base_url=base_url,
            model=model,
            mode=mode,
            system_prompt=system_prompt,
            timeout=timeout,
        )
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.model = config.model
        self.mode = config.mode
        self.system_prompt = config.system_prompt or DEFAULT_SYSTEM_PROMPT
        self.timeout = config.timeout
        self._opener = opener or urllib.request.build_opener()

    # ------------------------------------------------------------ 请求组装

    def build_body(self, prompt: MessagesInput, **options: Any) -> Dict[str, Any]:
        """组装上游请求体。

        - ``normal``：注入系统提示词（可用 ``system_prompt=`` 覆盖），可携带抽样参数与工具
        - ``passthrough``：**不新增任何提示词**，调用方的 ``messages`` 原样下发

        透传模式只做 3 件协议必需的事，其余一律不改写：

        1. 补 ``stream=True``（上游硬性要求，非流式会返回 11101）
        2. 补 ``model``（调用方未指定时使用默认模型）
        3. ``prompt`` 为字符串时包装成单条 ``user`` 消息

        需要下发上游私有字段时用 ``extra={...}``，会原样合并进请求体。
        """
        mode = normalize_mode(options.get("mode") or self.mode)
        messages = _to_messages(prompt)

        # 模型解析顺序：显式 model 参数 > 输入映射里的 model > 客户端默认 model
        model = options.get("model")
        if not model and isinstance(prompt, Mapping):
            model = prompt.get("model")

        body: Dict[str, Any] = {
            "model": model or self.model,
            # 上游只接受流式请求，这里始终以流式发起
            "stream": True,
            "messages": messages,
        }

        if mode == MODE_NORMAL:
            system_prompt = options.get("system_prompt") or self.system_prompt
            if system_prompt and not any(m.get("role") == "system" for m in messages):
                body["messages"] = [{"role": "system", "content": system_prompt}, *messages]

        # 透传模式只下发调用方显式传入的字段，不附加任何默认参数
        for key in _PASSTHROUGH_FIELDS:
            if options.get(key) is not None:
                body[key] = options[key]

        # extra：完全自定义字段，原样合并（可覆盖上方任意字段），用于透传上游私有参数
        extra = options.get("extra")
        if isinstance(extra, Mapping):
            body.update({str(key): value for key, value in extra.items()})

        return {"mode": mode, "body": body}

    # ------------------------------------------------------------ 公开接口

    def chat(self, prompt: MessagesInput, **options: Any) -> ChatResult:
        """模式 1：正常调用，返回聚合后的完整结果。"""
        return self._collect(prompt, **options)

    def chat_raw(self, prompt: MessagesInput, **options: Any) -> ChatResult:
        """模式 2：提示词透传，返回聚合后的完整结果。"""
        options["mode"] = MODE_PASSTHROUGH
        return self._collect(prompt, **options)

    def stream(self, prompt: MessagesInput, **options: Any) -> Iterator[StreamEvent]:
        """模式 1：正常调用，逐条产出流式事件。"""
        return self._stream(prompt, **options)

    def stream_raw(self, prompt: MessagesInput, **options: Any) -> Iterator[StreamEvent]:
        """模式 2：提示词透传，逐条产出流式事件。"""
        options["mode"] = MODE_PASSTHROUGH
        return self._stream(prompt, **options)

    # ------------------------------------------------------------ 调试辅助

    def preview(self, prompt: MessagesInput, **options: Any) -> Dict[str, Any]:
        """只组装请求体、不发起请求，返回最终发送给基座模型的 payload。"""
        return self.build_body(prompt, **options)["body"]

    def show_payload(self, prompt: MessagesInput, **options: Any) -> str:
        """打印（并返回）最终发送给基座模型的提示词，不会消耗额度。"""
        built = self.build_body(prompt, **options)
        text = format_payload(
            built["body"],
            mode=built["mode"],
            title=options.get("title") or "发送给基座模型的提示词",
        )
        print(text, file=options.get("file") or sys.stderr)
        return text

    # ------------------------------------------------------------ 内部实现

    def _collect(self, prompt: MessagesInput, **options: Any) -> ChatResult:
        result = ChatResult()
        for event in self._stream(prompt, **options):
            if event.type == "start":
                result.mode = event.mode
                result.model = event.model
            elif event.type == "meta":
                result.id = event.id or result.id
                result.model = event.model or result.model
            elif event.type == "text":
                result.content += event.text
            elif event.type == "reasoning":
                result.reasoning_content += event.text
            elif event.type == "tool_calls":
                result.tool_calls = event.tool_calls
            elif event.type == "done":
                result.finish_reason = event.finish_reason or result.finish_reason
                result.usage = event.usage or result.usage
        return result

    def _stream(self, prompt: MessagesInput, **options: Any) -> Iterator[StreamEvent]:
        built = self.build_body(prompt, **options)
        mode, body = built["mode"], built["body"]

        # debug=True 时，打印真正发送给基座模型的提示词
        if options.get("debug"):
            print_payload(
                body,
                mode=mode,
                title="发送给基座模型的提示词",
                file=options.get("debug_file"),
            )

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
            "User-Agent": "workbuddy-python/1.0",
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key,
            **{str(k): str(v) for k, v in (options.get("headers") or {}).items()},
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            response = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            raise _to_error(error.code, error.read().decode("utf-8", "replace")) from None
        except urllib.error.URLError as error:
            raise WorkBuddyError(f"请求上游失败：{error.reason}") from None

        with response:
            yield StreamEvent(type="start", mode=mode, model=body["model"])
            yield from self._parse_sse(response, options)

    @staticmethod
    def _parse_sse(response: Any, options: Mapping[str, Any]) -> Iterator[StreamEvent]:
        tool_calls: List[Dict[str, Any]] = []
        id_: Optional[str] = None
        model: Optional[str] = None
        usage: Optional[Dict[str, Any]] = None
        finish_reason: Optional[str] = None
        meta_sent = False

        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue

            payload_text = line[len("data:") :].strip()
            if payload_text == "[DONE]":
                break

            try:
                chunk = json.loads(payload_text)
            except json.JSONDecodeError as error:
                on_error = options.get("on_parse_error")
                if callable(on_error):
                    on_error(error, payload_text)
                continue

            # 上游也可能以 200 + 业务错误码返回
            if "code" in chunk and not chunk.get("choices"):
                raise _to_error(200, payload_text)

            if chunk.get("usage"):
                usage = chunk["usage"]
            if chunk.get("id"):
                id_ = chunk["id"]
            if chunk.get("model"):
                model = chunk["model"]
            if not meta_sent and (id_ or model):
                meta_sent = True
                yield StreamEvent(type="meta", id=id_, model=model)

            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    yield StreamEvent(type="reasoning", text=delta["reasoning_content"])
                if delta.get("content"):
                    yield StreamEvent(type="text", text=delta["content"])
                for call in delta.get("tool_calls") or []:
                    index = call.get("index", len(tool_calls))
                    while len(tool_calls) <= index:
                        tool_calls.append(
                            {
                                "id": None,
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )
                    current = tool_calls[index]
                    if call.get("id"):
                        current["id"] = call["id"]
                    if call.get("type"):
                        current["type"] = call["type"]
                    function = call.get("function") or {}
                    if function.get("name"):
                        current["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        current["function"]["arguments"] += function["arguments"]

        if tool_calls:
            yield StreamEvent(type="tool_calls", tool_calls=tool_calls)
        yield StreamEvent(
            type="done",
            id=id_,
            model=model,
            finish_reason=finish_reason,
            usage=usage,
        )


def create_client(**kwargs: Any) -> WorkBuddyClient:
    """``WorkBuddyClient`` 的快捷构造。"""
    return WorkBuddyClient(**kwargs)


__all__ = [
    "ChatResult",
    "DEFAULT_SYSTEM_PROMPT",
    "MODE_NORMAL",
    "MODE_PASSTHROUGH",
    "StreamEvent",
    "WorkBuddyClient",
    "WorkBuddyError",
    "create_client",
]

"""Lớp LLM trung lập nhà cung cấp + đếm token (dùng cho Critic, Baselines, Judge).

* `LLMClient` – giao thức tối thiểu: `complete(system, user) -> LLMResponse`.
* `LangChainLLM` – bọc bất kỳ chat model LangChain nào (Gemini, Groq, Ollama, OpenAI, Anthropic...).
* `llm_from_env()` – tạo model từ biến môi trường `LLM_PROVIDER`, `LLM_MODEL` (temperature=0).
* `UsageTracker` – đếm token/latency để log `Token Cost` lên MLflow.
* `ScriptedLLM` – LLM giả lập trả lời theo kịch bản (unit test, chạy offline).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, Sequence, Union


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    model: str = ""


class LLMClient(Protocol):
    model_name: str

    def complete(self, system: str, user: str) -> LLMResponse: ...


# ----------------------------------------------------------------------------


class LangChainLLM:
    """Bọc `BaseChatModel` của LangChain. Token lấy từ `usage_metadata` nếu provider trả về."""

    def __init__(self, chat_model, model_name: str = ""):
        self.chat_model = chat_model
        self.model_name = model_name or getattr(chat_model, "model_name", "") or getattr(chat_model, "model", "") or "unknown"

    def complete(self, system: str, user: str) -> LLMResponse:
        from langchain_core.messages import HumanMessage, SystemMessage

        start = time.perf_counter()
        msg = self.chat_model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        latency = time.perf_counter() - start
        usage = getattr(msg, "usage_metadata", None) or {}
        content = msg.content
        if isinstance(content, list):  # một số provider trả về list block
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return LLMResponse(
            text=str(content),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            latency_s=latency,
            model=self.model_name,
        )


def llm_from_env(temperature: float = 0.0) -> LangChainLLM:
    """VD: LLM_PROVIDER=google_genai LLM_MODEL=gemini-2.0-flash  |  LLM_PROVIDER=groq LLM_MODEL=llama-3.3-70b-versatile."""
    from langchain.chat_models import init_chat_model

    provider = os.environ.get("LLM_PROVIDER")
    model = os.environ.get("LLM_MODEL")
    if not model:
        raise RuntimeError("Chưa đặt biến môi trường LLM_MODEL (và LLM_PROVIDER).")
    chat = init_chat_model(model, model_provider=provider, temperature=temperature)
    return LangChainLLM(chat, model_name=f"{provider}:{model}" if provider else model)


# ----------------------------------------------------------------------------


@dataclass
class UsageRecord:
    component: str
    input_tokens: int
    output_tokens: int
    latency_s: float


class UsageTracker:
    """Bọc 1 LLMClient, ghi lại mọi lượt gọi. `tracker.for_component('critic')` trả client gắn nhãn."""

    def __init__(self, inner: LLMClient):
        self.inner = inner
        self.model_name = getattr(inner, "model_name", "unknown")
        self.records: List[UsageRecord] = []
        self._lock = threading.Lock()

    def complete(self, system: str, user: str, component: str = "llm") -> LLMResponse:
        resp = self.inner.complete(system, user)
        with self._lock:
            self.records.append(UsageRecord(component, resp.input_tokens, resp.output_tokens, resp.latency_s))
        return resp

    def for_component(self, component: str) -> "_TaggedClient":
        return _TaggedClient(self, component)

    def reset(self) -> None:
        with self._lock:
            self.records.clear()

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records)

    @property
    def calls(self) -> int:
        return len(self.records)

    def cost_usd(self, price_in_per_mtok: float = 0.0, price_out_per_mtok: float = 0.0) -> float:
        return (self.input_tokens * price_in_per_mtok + self.output_tokens * price_out_per_mtok) / 1_000_000


class _TaggedClient:
    def __init__(self, tracker: UsageTracker, component: str):
        self.tracker = tracker
        self.component = component
        self.model_name = tracker.model_name

    def complete(self, system: str, user: str) -> LLMResponse:
        return self.tracker.complete(system, user, component=self.component)


# ----------------------------------------------------------------------------


Reply = Union[str, Callable[[str, str], str]]


class ScriptedLLM:
    """LLM giả: trả lần lượt các câu trả lời (str hoặc hàm (system, user) -> str)."""

    def __init__(self, replies: Sequence[Reply] | Reply, model_name: str = "scripted", tokens_per_call=(100, 50)):
        self.replies = list(replies) if isinstance(replies, (list, tuple)) else [replies]
        self.model_name = model_name
        self.calls: List[tuple] = []
        self.tokens_per_call = tokens_per_call

    def complete(self, system: str, user: str) -> LLMResponse:
        idx = min(len(self.calls), len(self.replies) - 1)
        self.calls.append((system, user))
        r = self.replies[idx]
        text = r(system, user) if callable(r) else r
        return LLMResponse(text=text, input_tokens=self.tokens_per_call[0], output_tokens=self.tokens_per_call[1], model=self.model_name)


# ----------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def extract_json(text: str) -> Optional[dict]:
    """Lấy object JSON đầu tiên trong câu trả lời LLM (chịu được ```json fences``` và chữ thừa)."""
    if not text:
        return None
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for cand in candidates:
        cand = cand.strip()
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        start = cand.find("{")
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(cand)):
                ch = cand[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(cand[start : i + 1])
                            if isinstance(obj, dict):
                                return obj
                        except Exception:
                            pass
                        break
            start = cand.find("{", start + 1)
    return None

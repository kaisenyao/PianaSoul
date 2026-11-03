"""Thin wrapper over the OpenAI (Responses API) and Anthropic (Messages API) tool-calling chat.

Choose the model with PIANASOUL_MODEL, e.g. "openai:gpt-6-luna" or "anthropic:claude-haiku-5-5".
Without it, OpenAI is used when OPENAI_API_KEY is set, otherwise Anthropic.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

DEFAULTS = {"openai": "gpt-6-luna", "anthropic": "claude-haiku-5-5"}
# She thinks a little, on the models that think: her first sentence came quickest and cleanest so (M5.5 validation).
REASONING = {"effort": "low"}
THINKS = re.compile(r"(o\d|gpt-[5-9])")  # OpenAI's models that take reasoning settings


class BrokenOff(RuntimeError):
    """A streamed response that ended before it was over."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def model_spec() -> tuple[str, str]:
    spec = os.environ.get("PIANASOUL_MODEL", "").strip()
    if spec:
        provider, _, model = spec.partition(":")
        provider = provider.lower()
        if provider not in DEFAULTS:
            raise ValueError(f"PIANASOUL_MODEL must start with openai: or anthropic:, got {spec!r}")
        return provider, model or DEFAULTS[provider]
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", DEFAULTS["openai"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", DEFAULTS["anthropic"]
    raise ValueError("set OPENAI_API_KEY or ANTHROPIC_API_KEY (and optionally PIANASOUL_MODEL)")


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


class OpenAIChat:
    def __init__(self, model: str, instructions: str, tools: list[dict]):
        from openai import OpenAI

        self.client, self.model, self.instructions = OpenAI(), model, instructions
        self.tools = [{"type": "function", "name": t["name"], "description": t["description"],
                       "parameters": t["parameters"]} for t in tools]
        self.previous = self.turn_start = None
        self.earlier: list[dict] = []  # a conversation picked up again, sent before the first request
        # Called with (the words written, the tool's name) as the model, having written words, starts on its first
        # tool call: what it says it will do may be said while it does it.
        self.before_tool = None

    def _call(self, items: list) -> Reply:
        """The model's response, streamed (it is done sooner so): its words before a tool call go to before_tool as
        soon as it starts on the call. A stream that breaks off is asked for again, as the SDK does with a response
        that comes whole, unless its words were handed over already: they are never said twice."""
        from openai import APIConnectionError

        kwargs = {"previous_response_id": self.previous} if self.previous else {}
        if THINKS.match(self.model):
            kwargs["reasoning"] = REASONING
        for attempt in range(self.client.max_retries + 1):
            handed = []
            try:
                resp = self._stream(items, kwargs, handed)
                break
            except (APIConnectionError, BrokenOff):
                if handed or attempt == self.client.max_retries:
                    raise
        self.previous = resp.id
        calls = [ToolCall(o.call_id, o.name, _parse_args(o.arguments))
                 for o in resp.output if getattr(o, "type", "") == "function_call"]
        return Reply(resp.output_text or "", calls)

    def _stream(self, items: list, kwargs: dict, handed: list):
        """One streamed response, whole once it is over; handed gets the words given to before_tool."""
        resp, words, told = None, "", False
        for event in self.client.responses.create(model=self.model, instructions=self.instructions, input=items,
                                                  tools=self.tools, stream=True, **kwargs):
            if event.type == "response.output_text.delta":
                words += event.delta
            elif event.type == "response.output_item.added" and event.item.type == "function_call" and not told:
                told = True
                if words.strip() and self.before_tool:
                    handed.append(words.strip())
                    self.before_tool(words.strip(), event.item.name)
            elif event.type in {"response.completed", "response.incomplete"}:
                resp = event.response
            elif event.type in {"response.failed", "error"}:
                error = getattr(getattr(event, "response", None), "error", None) or event
                raise RuntimeError(f"the model failed: {getattr(error, 'message', error)}")
        if resp is None:
            raise BrokenOff("the model's response ended before it was complete")
        return resp

    def send_user(self, text: str) -> Reply:
        self.turn_start = self.previous
        return self._call([*([] if self.previous else self.earlier), {"role": "user", "content": text}])

    def send_tool_results(self, results: list[tuple[ToolCall, str]]) -> Reply:
        return self._call([{"type": "function_call_output", "call_id": c.id, "output": out} for c, out in results])

    def discard_turn(self) -> None:
        """Forget the latest request and what followed it; the conversation before it stays."""
        self.previous = self.turn_start

    def reset(self) -> None:
        self.previous = self.turn_start = None
        self.earlier = []

    def load(self, lines: list[tuple[str, str]]) -> None:
        """Start from an earlier conversation: (role, text) lines, role "user" or "assistant"."""
        self.reset()
        self.earlier = [{"role": role, "content": text} for role, text in lines]


class AnthropicChat:
    def __init__(self, model: str, instructions: str, tools: list[dict]):
        import anthropic

        self.client, self.model, self.instructions = anthropic.Anthropic(), model, instructions
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                      for t in tools]
        self.messages: list[dict] = []
        self.turn_start = 0
        self.before_tool = None  # as OpenAIChat's; never called, the response coming whole

    def _call(self) -> Reply:
        resp = self.client.messages.create(model=self.model, max_tokens=2000, system=self.instructions,
                                           messages=self.messages, tools=self.tools)
        self.messages.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, _parse_args(b.input)) for b in resp.content if b.type == "tool_use"]
        return Reply(text, calls)

    def send_user(self, text: str) -> Reply:
        self.turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": text})
        return self._call()

    def send_tool_results(self, results: list[tuple[ToolCall, str]]) -> Reply:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": out} for c, out in results]})
        return self._call()

    def discard_turn(self) -> None:
        """Forget the latest request and what followed it; the conversation before it stays."""
        del self.messages[self.turn_start:]

    def reset(self) -> None:
        self.messages = []
        self.turn_start = 0

    def load(self, lines: list[tuple[str, str]]) -> None:
        """Start from an earlier conversation: (role, text) lines, role "user" or "assistant"."""
        self.reset()
        for role, text in lines:  # the Messages API wants the roles to take turns, the user first
            if self.messages and self.messages[-1]["role"] == role:
                self.messages[-1]["content"] += "\n" + text
            elif self.messages or role == "user":
                self.messages.append({"role": role, "content": text})
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()  # a request left unanswered; the next one follows her last reply


def make_chat(instructions: str, tools: list[dict]):
    provider, model = model_spec()
    cls = OpenAIChat if provider == "openai" else AnthropicChat
    return cls(model, instructions, tools), f"{'OpenAI' if provider == 'openai' else 'Anthropic'} {model}"


def complete_json(prompt: str) -> dict:
    """One-shot request that should answer with a JSON object."""
    provider, model = model_spec()
    if provider == "openai":
        from openai import OpenAI

        text = OpenAI().responses.create(model=model, input=prompt).output_text
    else:
        import anthropic

        resp = anthropic.Anthropic().messages.create(model=model, max_tokens=4000,
                                                     messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if b.type == "text")
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}

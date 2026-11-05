"""Thin wrapper over the OpenAI and Alibaba Cloud Model Studio (Responses API) and Anthropic (Messages API)
tool-calling chat.

Choose the provider with PIANASOUL_MODEL: "openai", "bailian" or "anthropic", alone or with a model after a colon,
e.g. "openai:gpt-6-luna" or "bailian:qwen3.8-max". Without it, OpenAI is used when OPENAI_API_KEY is set, otherwise
Anthropic. "bailian" is Alibaba Cloud Model Studio, by its name in China; its key is DASHSCOPE_API_KEY (DashScope
being its API), and it is asked in the region the key was made in (DASHSCOPE_REGION: beijing, the default, or
singapore).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass, field

# She thinks a little, on the models that think: her first sentence came quickest and cleanest so (M5.5 validation).
REASONING = {"effort": "low"}
THINKS = re.compile(r"(o\d|gpt-[5-9])")  # OpenAI's models that take reasoning settings


@dataclass(frozen=True)
class Provider:
    name: str  # as the start-up lines say it
    model: str  # unless PIANASOUL_MODEL names another
    key: str  # the environment variable with its API key
    hears: str  # the provider whose speech recognition hears her requests
    always: dict = field(default_factory=dict)  # what each request to it says besides (Responses API parameters)


PROVIDERS = {
    "openai": Provider("OpenAI", "gpt-6-luna", "OPENAI_API_KEY", hears="openai"),
    "anthropic": Provider("Anthropic", "claude-haiku-5-5", "ANTHROPIC_API_KEY", hears="openai"),
    # Her model on Model Studio is DeepSeek's, chosen by her real-model check (2026-11-04): 104 of 108 against
    # OpenAI's 105, nothing said and not done once the guard held such replies, and the cheapest of the models that
    # passed. Model Studio has it through the Responses API in both regions (its DeepSeek page, 2026-10-27). Its models
    # think unless told not to: thinking, Qwen spent all 16 tokens of the app's key check on it (smoke test,
    # 2026-11-03); told none, DeepSeek does not think (2026-11-04).
    "bailian": Provider("Model Studio", "deepseek-v4.1-flash", "DASHSCOPE_API_KEY", hears="bailian",
                        always={"reasoning": {"effort": "none"}}),
}
# Model Studio's models that think whatever they are told, and the least they take: told none, GLM says it "does
# not support 'reasoning.effort' to be 'none'" (2026-11-04).
LEAST_THINKING = {"glm-5.3": {"reasoning": {"effort": "low"}}}
# How the application's word on a reply held back is given (OpenAIChat.ask_again): as instructions, not as the
# user's words.
NOTE_ROLE = "developer"
# Model Studio's address in each region; a key works only in the region it was made in.
DASHSCOPE_HOSTS = {"beijing": "dashscope.aliyuncs.com", "singapore": "dashscope-intl.aliyuncs.com"}
# Model Studio's code for words that did not pass its content inspection, those asked or those written.
REFUSED = "DataInspectionFailed"
# How long a request to her model may wait (limits). The SDKs' own limit is 10 minutes for each read: after the Mac
# slept, a reply streamed over a connection that had gone held its request half an hour. CONNECT_S to connect, as the
# SDKs have it; then for each read, CHAT_READ_S in her chat, between the events of a streamed reply (Anthropic's comes
# whole, a few sentences), and ANSWER_READ_S for complete_json, whose answer comes whole. A request quiet for longer
# fails as one whose connection went: it is asked again as such a request is (the SDKs' retries, OpenAIChat._call), and
# then she says she could not answer. Timed on this Mac on 2026-11-04 (the end-to-end runs: 256 streamed calls, 70 of
# complete_json): the first event came after 0.6 s (OpenAI, median; 4.6 s for 99 in 100) or 1.2 s (Model Studio, from
# the US; 6.8 s), and no wait between two events was longer than 7.1 s, under a third of CHAT_READ_S; complete_json
# named a piece in 8.6 s at most, and 30 pieces at once (library.enrich's batch, timed for this) in 29 to 35 s on
# OpenAI, which thinks first, and 10 to 12 s on Model Studio: ANSWER_READ_S is two and a half times the longest.
CONNECT_S, CHAT_READ_S, ANSWER_READ_S = 5.0, 20.0, 90.0


class BrokenOff(RuntimeError):
    """A streamed response that ended before it was over."""


class Refused(RuntimeError):
    """A request the provider would not take for its words (REFUSED): asked again, it would be refused again."""


def is_refusal(code) -> bool:
    """An error's code is REFUSED, as Model Studio spells it either way (data_inspection_failed in compatible mode)."""
    return str(code or "").replace("_", "").casefold() == REFUSED.casefold()


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens: tuple[int, int, int] = (0, 0, 0)  # read (all of them), of those read from the provider's cache, written


def chosen_provider() -> str:
    """Her model line: the provider PIANASOUL_MODEL names; without it OpenAI, or Anthropic when only its key is set.
    ValueError when PIANASOUL_MODEL names none of them."""
    spec = os.environ.get("PIANASOUL_MODEL", "").strip()
    if not spec:
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY") else "openai"
    name = spec.partition(":")[0].lower()
    if name not in PROVIDERS:
        raise ValueError(f"PIANASOUL_MODEL must be openai, bailian or anthropic, alone or with a model after a colon, "
                         f"got {spec!r}")
    return name


def model_spec() -> tuple[str, str]:
    """(provider, model): the model PIANASOUL_MODEL names after the colon, or the provider's own. Without
    PIANASOUL_MODEL, ValueError unless OpenAI's or Anthropic's key is set."""
    spec = os.environ.get("PIANASOUL_MODEL", "").strip()
    provider = chosen_provider()
    if not spec and not os.environ.get(PROVIDERS[provider].key):
        raise ValueError("set OPENAI_API_KEY or ANTHROPIC_API_KEY, or PIANASOUL_MODEL=bailian and DASHSCOPE_API_KEY")
    return provider, spec.partition(":")[2] or PROVIDERS[provider].model


def settings(provider: str, model: str) -> dict:
    """What each request to this provider's model says besides (Responses API parameters)."""
    return {**PROVIDERS[provider].always, **LEAST_THINKING.get(model, {})}


def keys_needed(provider: str) -> list[str]:
    """The API keys her setup needs with this provider: its own, then that of the provider who hears her requests
    (OpenAI's, with Anthropic's chat)."""
    return list(dict.fromkeys([PROVIDERS[provider].key, PROVIDERS[PROVIDERS[provider].hears].key]))


def api_key(provider: str) -> str:
    """The provider's API key; ValueError when it is not set."""
    name = PROVIDERS[provider].key
    if not os.environ.get(name):
        raise ValueError(f"{name} is not set")
    return os.environ[name]


def dashscope_host(variable: str = "DASHSCOPE_REGION") -> str:
    """Model Studio's address for the region this variable names (Beijing when it is not set)."""
    region = os.environ.get(variable, "").strip().lower() or "beijing"
    if region not in DASHSCOPE_HOSTS:
        raise ValueError(f"{variable} must be beijing or singapore, got {region!r}")
    return DASHSCOPE_HOSTS[region]


def limits(sdk, read_s: float):
    """How long a request to her model may wait, as the SDK (openai or anthropic) takes it: CONNECT_S to connect, and
    read_s for each read or write."""
    return sdk.Timeout(read_s, connect=CONNECT_S)


def openai_client(provider: str):
    """An OpenAI SDK client for a provider with the Responses API, with her chat's limits: OpenAI's as the SDK makes it
    from the environment; Model Studio's with its own key and its region's address, never OPENAI_API_KEY or
    OPENAI_BASE_URL."""
    import openai

    timeout = limits(openai, CHAT_READ_S)
    if provider == "bailian":
        return openai.OpenAI(api_key=api_key(provider), base_url=f"https://{dashscope_host()}/compatible-mode/v1",
                             timeout=timeout)
    return openai.OpenAI(timeout=timeout)


@contextlib.contextmanager
def refusals():
    """An error the provider gives for words it refuses, raised as Refused, which says its code."""
    from openai import APIError

    try:
        yield
    except APIError as exc:
        if not is_refusal(exc.code):
            raise
        raise Refused(f"{REFUSED}: {exc.message}") from exc


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


class OpenAIChat:
    def __init__(self, model: str, instructions: str, tools: list[dict], provider: str = "openai"):
        self.client, self.model, self.instructions = openai_client(provider), model, instructions
        self.always = settings(provider, model)
        self.tools = [{"type": "function", "name": t["name"], "description": t["description"],
                       "parameters": t["parameters"]} for t in tools]
        self.previous = self.turn_start = None
        self.earlier: list[dict] = []  # a conversation picked up again, sent before the first request
        self.asked: tuple[str | None, list] = (None, [])  # the latest response asked for: after which, and with what
        # Called with (the words written, the tool's name) as the model, having written words, starts on its first
        # tool call: what it says it will do may be said while it does it. True: they were said.
        self.before_tool = None

    def _call(self, items: list) -> Reply:
        """The model's response, streamed (it is done sooner so): its words before a tool call go to before_tool as
        soon as it starts on the call. A stream that breaks off, or is quiet for CHAT_READ_S, is asked for again, as
        the SDK does with a response that comes whole, unless its words were handed over already: they are never said
        twice."""
        from openai import APIConnectionError

        self.asked = self.previous, items
        kwargs = {"previous_response_id": self.previous} if self.previous else {}
        if THINKS.match(self.model):
            kwargs["reasoning"] = REASONING
        kwargs.update(self.always)
        with refusals():
            for attempt in range(self.client.max_retries + 1):
                handed = []  # connecting, the SDK's own retries do; a stream that breaks off while read, these
                events = self.client.responses.create(model=self.model, instructions=self.instructions, input=items,
                                                      tools=self.tools, stream=True, **kwargs)
                try:
                    resp = self._read(events, handed)
                    break
                except (APIConnectionError, BrokenOff):
                    if handed or attempt == self.client.max_retries:
                        raise
        self.previous = resp.id
        calls = [ToolCall(o.call_id, o.name, _parse_args(o.arguments))
                 for o in resp.output if getattr(o, "type", "") == "function_call"]
        used = resp.usage  # as billed; the cached part is cheaper (Model Studio's implicit cache)
        tokens = (used.input_tokens, getattr(used.input_tokens_details, "cached_tokens", 0) or 0,
                  used.output_tokens) if used else (0, 0, 0)
        return Reply(resp.output_text or "", calls, tokens)

    def _read(self, events, handed: list):
        """One streamed response, whole once it is over; handed gets the words before_tool said."""
        resp, words, told = None, "", False
        for event in events:
            if event.type == "response.output_text.delta":
                words += event.delta
            elif event.type == "response.output_item.added" and event.item.type == "function_call" and not told:
                told = True
                if words.strip() and self.before_tool and self.before_tool(words.strip(), event.item.name):
                    handed.append(words.strip())
            elif event.type in {"response.completed", "response.incomplete"}:
                resp = event.response
            elif event.type in {"response.failed", "error"} or getattr(event, "code", None):  # Model Studio's: no type
                error = getattr(getattr(event, "response", None), "error", None) or event
                message = getattr(error, "message", error)
                if is_refusal(getattr(error, "code", None)):
                    raise Refused(f"{REFUSED}: {message}")
                raise RuntimeError(f"the model failed: {message}")
        if resp is None:
            raise BrokenOff("the model's response ended before it was complete")
        return resp

    def send_user(self, text: str) -> Reply:
        self.turn_start = self.previous
        return self._call([*([] if self.previous else self.earlier), {"role": "user", "content": text}])

    def send_tool_results(self, results: list[tuple[ToolCall, str]]) -> Reply:
        return self._call([{"type": "function_call_output", "call_id": c.id, "output": out} for c, out in results])

    def ask_again(self, note: str) -> Reply:
        """The latest response asked for once more, with the application's word on it (note): from where it was
        asked, so that the reply held back is not in the conversation, and words said before it stay there."""
        self.previous, items = self.asked
        return self._call([*items, {"role": NOTE_ROLE, "content": note}])

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

        self.client = anthropic.Anthropic(timeout=limits(anthropic, CHAT_READ_S))
        self.model, self.instructions = model, instructions
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
        cached = resp.usage.cache_read_input_tokens or 0
        read = resp.usage.input_tokens + cached + (resp.usage.cache_creation_input_tokens or 0)
        return Reply(text, calls, (read, cached, resp.usage.output_tokens))

    def send_user(self, text: str) -> Reply:
        self.turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": text})
        return self._call()

    def send_tool_results(self, results: list[tuple[ToolCall, str]]) -> Reply:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": out} for c, out in results]})
        return self._call()

    def ask_again(self, note: str) -> Reply:
        """As OpenAIChat's: the note joins what the latest response answered (the Messages API has no other turn
        for it)."""
        self.messages.pop()
        asked = self.messages[-1]["content"]
        self.messages[-1] = {"role": "user", "content": [
            *(asked if isinstance(asked, list) else [{"type": "text", "text": asked}]), {"type": "text", "text": note}]}
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
    chat = (AnthropicChat(model, instructions, tools) if provider == "anthropic" else
            OpenAIChat(model, instructions, tools, provider))
    return chat, f"{PROVIDERS[provider].name} {model}"


def complete_json(prompt: str) -> dict:
    """One-shot request that should answer with a JSON object, whole (ANSWER_READ_S); Refused when the provider
    refuses its words."""
    provider, model = model_spec()
    if provider == "anthropic":
        import anthropic

        resp = anthropic.Anthropic(timeout=limits(anthropic, ANSWER_READ_S)).messages.create(
            model=model, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if b.type == "text")
    else:
        import openai

        with refusals():
            text = openai_client(provider).responses.create(model=model, input=prompt,
                                                            timeout=limits(openai, ANSWER_READ_S),
                                                            **settings(provider, model)).output_text
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}

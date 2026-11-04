"""Her chat model's client: responses streamed, the words written before a tool call handed over as the model starts
on it, thinking a little on the models that think; her model line (OpenAI, Model Studio or Anthropic) and the keys it
needs.
No paid APIs: the responses are made up."""
import contextlib
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import httpx2

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import llm


def events(*words, call=None, end="response.completed"):
    """A streamed response: its words, then a function call (name, arguments), then its end."""
    out = [NS(type="response.output_text.delta", delta=w) for w in words]
    output = []
    if call:
        out.append(NS(type="response.output_item.added", item=NS(type="function_call", name=call[0])))
        output.append(NS(type="function_call", call_id="c1", name=call[0], arguments=call[1]))
    usage = NS(input_tokens=5149, input_tokens_details=NS(cached_tokens=5120), output_tokens=47)
    response = NS(id="r1", output=output, output_text="".join(words), status="completed", error=None, usage=usage)
    out.append(NS(type=end, response=response))
    return out


class OpenAIChatTests(unittest.TestCase):
    def chat(self, model="gpt-6-luna", stream=()):
        with patch.dict(os.environ, OPENAI_API_KEY="not a key"):
            chat = llm.OpenAIChat(model, "instructions", [])
        chat.client = Mock(max_retries=2)
        chat.client.responses.create.return_value = iter(stream)
        return chat

    def test_the_words_before_a_tool_call_are_handed_over_as_the_model_starts_on_it(self):
        chat = self.chat(stream=events("我找", "找看～", call=("search_library", '{"query": "夜曲"}')))
        handed = []
        chat.before_tool = lambda words, tool: handed.append((words, tool, chat.previous))
        reply = chat.send_user("弹一首夜曲")
        self.assertEqual(handed, [("我找找看～", "search_library", None)], 'Before the response is over')
        self.assertEqual((reply.text, [(c.name, c.arguments) for c in reply.tool_calls]),
                         ("我找找看～", [("search_library", {"query": "夜曲"})]))
        self.assertEqual(chat.previous, "r1")
        sent = chat.client.responses.create.call_args.kwargs
        self.assertEqual((sent["stream"], sent["reasoning"]), (True, {"effort": "low"}))

    def test_each_reply_says_the_tokens_read_of_them_cached_and_written(self):
        self.assertEqual(self.chat(stream=events("好呀。")).send_user("你好").tokens, (5149, 5120, 47))

    def test_a_reply_held_back_is_asked_for_again_from_where_it_was_with_the_applications_word(self):
        chat = self.chat()
        chat.client.responses.create.side_effect = [iter(events("好呀。")), iter(events("这就取消～")),
                                                    iter(events(call=("control_playback", '{"action": "cancel"}')))]
        held = "Your reply was held back."
        chat.send_user("你好")
        chat.send_user("取消吧")
        reply = chat.ask_again(held)
        sent = chat.client.responses.create.call_args.kwargs
        self.assertEqual((sent["input"], sent["previous_response_id"]),
                         ([{"role": "user", "content": "取消吧"}, {"role": "developer", "content": held}], "r1"),
                         'Not after the reply held back: it is never in the conversation')
        self.assertEqual([c.name for c in reply.tool_calls], ["control_playback"])

    def test_nothing_is_handed_over_without_words_or_without_a_tool_call(self):
        for stream in (events(call=("play_recording", '{"id": 3}')), events("好呀，", "想听什么？")):
            chat = self.chat(stream=stream)
            chat.before_tool = Mock()
            chat.send_user("弹一下晴天")
            chat.before_tool.assert_not_called()

    def test_a_model_that_does_not_think_is_not_told_how_much_to(self):
        chat = self.chat(model="gpt-4.1", stream=events("好。"))
        chat.send_user("你好")
        self.assertNotIn("reasoning", chat.client.responses.create.call_args.kwargs)

    def test_a_response_that_fails_or_breaks_off_is_an_error_and_the_conversation_stays(self):
        for stream in (events("好", end="response.failed"), events("好")[:-1]):
            chat = self.chat(stream=stream)
            chat.previous = "r0"
            with self.assertRaises(RuntimeError):
                chat.send_user("你好")
            self.assertEqual(chat.previous, "r0")

    def test_a_stream_that_breaks_off_is_asked_for_again_unless_her_words_were_said(self):
        chat = self.chat()
        chat.client.responses.create.side_effect = [iter(events("好")[:-1]), iter(events("好呀。"))]
        self.assertEqual(chat.send_user("你好").text, "好呀。")
        chat = self.chat()
        chat.before_tool = Mock(return_value=True)  # said
        broken = events("我找找～", call=("search_library", '{"query": "夜曲"}'))[:-1]
        chat.client.responses.create.side_effect = [iter(broken), iter(events("我找找～"))]
        with self.assertRaises(llm.BrokenOff):
            chat.send_user("弹一首夜曲")
        chat.before_tool.assert_called_once()
        self.assertEqual(chat.client.responses.create.call_count, 1, 'Said already: not asked again')
        chat = self.chat()
        chat.before_tool = Mock(return_value=False)  # words with an action: not said, so it may be asked again
        broken = events("这就弹～", call=("play_recording", '{"id": 3}'))[:-1]
        chat.client.responses.create.side_effect = [iter(broken), iter(events("这就弹～", call=("play_recording", '{"id": 3}')))]
        self.assertEqual([c.name for c in chat.send_user("弹晴天").tool_calls], ["play_recording"])

    def test_connecting_is_left_to_the_sdk_s_own_retries(self):
        from openai import APIConnectionError
        chat = self.chat()
        chat.client.responses.create.side_effect = APIConnectionError(request=Mock())
        with self.assertRaises(APIConnectionError):
            chat.send_user("你好")
        self.assertEqual(chat.client.responses.create.call_count, 1, 'Not again on top of them')

    def test_words_the_provider_refuses_end_that_request_saying_why(self):
        refused = NS(code="DataInspectionFailed", message="Output data may contain inappropriate content.")
        failed = NS(type="response.failed", response=NS(error=refused))
        for end in (NS(type="error", **vars(refused)), failed):
            chat = self.chat(stream=[*events("好")[:-1], end])
            with self.assertRaisesRegex(llm.Refused, "^DataInspectionFailed: Output data"):
                chat.send_user("你好")
        failed.response.error = NS(code="server_error", message="try again")
        chat = self.chat(stream=[failed])
        with self.assertRaises(RuntimeError) as raised:
            chat.send_user("你好")
        self.assertEqual((type(raised.exception), str(raised.exception)), (RuntimeError, "the model failed: try again"),
                         'Any other failure as before')


class AnthropicChatTests(unittest.TestCase):
    def test_a_reply_held_back_is_asked_for_again_with_the_applications_word_in_its_request(self):
        with patch.dict(os.environ, ANTHROPIC_API_KEY="not a key"):
            chat = llm.AnthropicChat("claude-haiku-5-5", "instructions", [])
        usage = NS(input_tokens=10, cache_read_input_tokens=5000, cache_creation_input_tokens=0, output_tokens=7)
        chat.client = Mock()
        chat.client.messages.create.return_value = NS(content=[NS(type="text", text="好。")], usage=usage)
        chat.send_user("取消吧")
        self.assertEqual(chat.ask_again("Your reply was held back.").tokens, (5010, 5000, 7))
        self.assertEqual(chat.messages[0], {"role": "user", "content": [
            {"type": "text", "text": "取消吧"}, {"type": "text", "text": "Your reply was held back."}]})
        self.assertEqual([m["role"] for m in chat.messages], ["user", "assistant"], 'The reply held back is gone')


# The variables that choose her model line, and where it is asked.
LINE = {"PIANASOUL_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_REGION",
        "OPENAI_BASE_URL"}


@contextlib.contextmanager
def line(**values):
    """Only these of LINE set, as if the others were not."""
    with patch.dict(os.environ, values):
        for name in LINE.difference(values):
            os.environ.pop(name, None)
        yield


def completed(text="", response_id="r1") -> dict:
    """A response as the Responses API gives it: its id and its words."""
    return {"id": response_id, "object": "response", "status": "completed", "output": [
        {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": text, "annotations": []}]}]}


def streamed(*data: dict) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                           content="".join(f"data: {json.dumps(d)}\n\n" for d in data).encode())


def said(text: str, response_id="r1") -> httpx2.Response:
    """Her answer, streamed: its words, then the response."""
    return streamed({"type": "response.output_text.delta", "delta": text},
                    {"type": "response.completed", "response": completed(text, response_id)})


def refused() -> httpx2.Response:
    """As Model Studio refuses words: HTTP 400, its code at the top."""
    return httpx2.Response(400, json={"code": "DataInspectionFailed", "request_id": "r",
                                      "message": "Input data may contain inappropriate content."})


class Server:
    """A made-up Responses API, OpenAI's or Model Studio's, reached through the SDK as the app reaches it: each
    request kept, and answered in turn. While it is on (with), every client llm makes goes to it."""

    def __init__(self, *answers: httpx2.Response):
        self.answers, self.requests = list(answers), []
        self.http = httpx2.Client(transport=httpx2.MockTransport(self.answer))

    def answer(self, request):
        self.requests.append(request)
        return self.answers.pop(0)

    def sent(self, i=-1) -> tuple[str, str, dict]:
        """A request: where it went, its authorization and its body."""
        request = self.requests[i]
        return str(request.url), request.headers["authorization"], json.loads(request.content)

    def __enter__(self):
        made = llm.openai_client
        self.made = patch.object(llm, "openai_client",
                                 lambda provider: made(provider).with_options(http_client=self.http))
        self.made.start()
        return self

    def __exit__(self, *_):
        self.made.stop()


class ModelLineTests(unittest.TestCase):
    """Who answers her and hears her, and which keys that needs."""

    def test_she_is_openai_unless_her_model_line_names_another_or_only_anthropic_has_a_key(self):
        for values, chosen in (({"OPENAI_API_KEY": "o"}, ("openai", "gpt-6-luna")),
                               ({"ANTHROPIC_API_KEY": "a"}, ("anthropic", "claude-haiku-5-5")),
                               ({"OPENAI_API_KEY": "o", "ANTHROPIC_API_KEY": "a"}, ("openai", "gpt-6-luna")),
                               ({"PIANASOUL_MODEL": "bailian"}, ("bailian", "deepseek-v4.1-flash")),
                               ({"PIANASOUL_MODEL": "Bailian:qwen3.8-max", "OPENAI_API_KEY": "o"},
                                ("bailian", "qwen3.8-max")),
                               ({"PIANASOUL_MODEL": "openai:gpt-4.1"}, ("openai", "gpt-4.1"))):
            with line(**values):
                self.assertEqual(llm.model_spec(), chosen, values)
        with line(DASHSCOPE_API_KEY="d"), self.assertRaisesRegex(ValueError, "or PIANASOUL_MODEL=bailian and"):
            llm.model_spec()  # a DashScope key alone does not choose Model Studio
        with line(PIANASOUL_MODEL="gemini", OPENAI_API_KEY="o"), self.assertRaisesRegex(ValueError, "openai, bailian"):
            llm.chosen_provider()

    def test_each_provider_needs_its_own_key_and_anthropic_openai_s_too_to_hear(self):
        self.assertEqual([llm.keys_needed(p) for p in ("openai", "bailian", "anthropic")],
                         [["OPENAI_API_KEY"], ["DASHSCOPE_API_KEY"], ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]])
        with line(OPENAI_API_KEY="o"), self.assertRaisesRegex(ValueError, "^DASHSCOPE_API_KEY is not set$"):
            llm.openai_client("bailian")  # never OpenAI's key instead

    def test_model_studio_is_asked_in_the_region_its_key_was_made_in(self):
        for region, host in (("", "dashscope.aliyuncs.com"), ("beijing", "dashscope.aliyuncs.com"),
                             (" Singapore ", "dashscope-intl.aliyuncs.com")):
            with line(DASHSCOPE_REGION=region):
                self.assertEqual(llm.dashscope_host(), host)
        with line(DASHSCOPE_REGION="hangzhou"), self.assertRaisesRegex(ValueError, "beijing or singapore"):
            llm.dashscope_host()

    def test_the_start_up_lines_name_the_provider_and_its_model(self):
        for values, label in (({"OPENAI_API_KEY": "o"}, "OpenAI gpt-6-luna"),
                              ({"PIANASOUL_MODEL": "bailian", "DASHSCOPE_API_KEY": "d"},
                               "Model Studio deepseek-v4.1-flash"),
                              ({"PIANASOUL_MODEL": "anthropic", "ANTHROPIC_API_KEY": "a"},
                               "Anthropic claude-haiku-5-5")):
            with line(**values):
                chat, made = llm.make_chat("instructions", [])
            self.assertEqual(made, label)
            self.assertIsInstance(chat, llm.AnthropicChat if label.startswith("Anthropic") else llm.OpenAIChat)


class ModelStudioTests(unittest.TestCase):
    """Her model on Model Studio, and OpenAI as before, through a made-up server."""

    def test_model_studio_answers_streamed_with_its_own_key_and_address_and_never_thinks(self):
        with line(PIANASOUL_MODEL="bailian", DASHSCOPE_API_KEY="dashscope-test", OPENAI_API_KEY="openai-test",
                  OPENAI_BASE_URL="https://elsewhere.example/v1"), Server(said("好呀。"), said("在呢。", "r2")) as server:
            chat, _ = llm.make_chat("instructions", [])
            self.assertEqual(chat.send_user("你好").text, "好呀。")
            chat.send_user("还在吗")
        where, key, body = server.sent(0)
        self.assertEqual((where, key), ("https://dashscope.aliyuncs.com/compatible-mode/v1/responses",
                                        "Bearer dashscope-test"), 'Never OPENAI_BASE_URL or OpenAI\'s key')
        self.assertEqual((body["model"], body["stream"], body["reasoning"]),
                         ("deepseek-v4.1-flash", True, {"effort": "none"}))
        self.assertEqual(server.sent()[2]["previous_response_id"], "r1")

    def test_openai_is_asked_as_before(self):
        with line(OPENAI_API_KEY="openai-test"), Server(said("好呀。")) as server:
            llm.make_chat("instructions", [])[0].send_user("你好")
        self.assertEqual(server.sent(), ("https://api.openai.com/v1/responses", "Bearer openai-test", {
            "model": "gpt-6-luna", "instructions": "instructions", "input": [{"role": "user", "content": "你好"}],
            "tools": [], "stream": True, "reasoning": {"effort": "low"}}))
        with line(OPENAI_API_KEY="openai-test"), Server(httpx2.Response(200, json=completed('{"a": 1}'))) as server:
            self.assertEqual(places.REAL_COMPLETE_JSON("Name it."), {"a": 1})
        self.assertEqual(server.sent()[2], {"model": "gpt-6-luna", "input": "Name it."}, 'Naming pieces, as before')

    def test_naming_pieces_asks_her_model_on_model_studio_in_its_region_not_to_think(self):
        with line(PIANASOUL_MODEL="bailian", DASHSCOPE_API_KEY="dashscope-test", DASHSCOPE_REGION="singapore"), \
                Server(httpx2.Response(200, json=completed('{"piece": "晴天"}'))) as server:
            self.assertEqual(places.REAL_COMPLETE_JSON("Name it."), {"piece": "晴天"})
        self.assertEqual(server.sent(), ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/responses",
                                         "Bearer dashscope-test", {"model": "deepseek-v4.1-flash", "input": "Name it.",
                                                                   "reasoning": {"effort": "none"}}))

    def test_words_model_studio_refuses_fail_that_request_saying_why_and_are_not_asked_again(self):
        mid_answer = streamed({"type": "response.output_text.delta", "delta": "好"}, {"error": {
            "code": "DataInspectionFailed", "message": "Output data may contain inappropriate content."}})
        with line(PIANASOUL_MODEL="bailian", DASHSCOPE_API_KEY="dashscope-test"), \
                Server(said("好呀。"), refused(), mid_answer, refused()) as server:
            chat, _ = llm.make_chat("instructions", [])
            chat.send_user("你好")
            for _ in range(2):
                with self.assertRaisesRegex(llm.Refused, "^DataInspectionFailed: "):
                    chat.send_user("...")
                chat.discard_turn()  # as her conversation does
                self.assertEqual(chat.previous, "r1", 'The conversation before it stays')
            with self.assertRaisesRegex(llm.Refused, "Input data may contain inappropriate content"):
                places.REAL_COMPLETE_JSON("Name it.")
        self.assertEqual(len(server.requests), 4, 'Each asked once: the SDK does not ask again')

    def test_an_error_model_studio_streams_without_a_type_is_said_as_it_is_and_not_asked_again(self):
        unsupported = streamed({"code": "InvalidParameter", "message": "Unsupported model: MiniMax/MiniMax-M3.",
                                "request_id": "r"})  # as Model Studio streams it, 2026-11-04
        with line(PIANASOUL_MODEL="bailian:MiniMax/MiniMax-M3", DASHSCOPE_API_KEY="dashscope-test"), \
                Server(unsupported) as server:
            with self.assertRaisesRegex(RuntimeError, "^the model failed: Unsupported model: MiniMax/MiniMax-M3"):
                llm.make_chat("instructions", [])[0].send_user("你好")
        self.assertEqual(len(server.requests), 1, 'Not taken for a stream that broke off')

    def test_a_model_that_always_thinks_is_told_to_think_least(self):
        with line(PIANASOUL_MODEL="bailian:glm-5.3", DASHSCOPE_API_KEY="dashscope-test"), \
                Server(said("好呀。"), httpx2.Response(200, json=completed("{}"))) as server:
            llm.make_chat("instructions", [])[0].send_user("你好")
            places.REAL_COMPLETE_JSON("Name it.")
        self.assertEqual([server.sent(i)[2]["reasoning"] for i in (0, 1)], [{"effort": "low"}] * 2)

    def test_a_refusal_spelt_as_in_compatible_mode_is_one_too(self):
        in_error = httpx2.Response(400, json={"error": {"code": "data_inspection_failed", "type": "data_inspection_failed",
                                                        "message": "Input data may contain inappropriate content."}})
        mid_answer = streamed({"type": "response.output_text.delta", "delta": "好"}, {
            "type": "error", "code": "data_inspection_failed", "message": "Output data may contain inappropriate content."})
        with line(PIANASOUL_MODEL="bailian", DASHSCOPE_API_KEY="dashscope-test"), Server(in_error, mid_answer):
            with self.assertRaisesRegex(llm.Refused, "^DataInspectionFailed: .*Input data may contain"):
                places.REAL_COMPLETE_JSON("Name it.")
            with self.assertRaisesRegex(llm.Refused, "^DataInspectionFailed: Output data may contain"):
                llm.make_chat("instructions", [])[0].send_user("你好")


if __name__ == "__main__":
    unittest.main()

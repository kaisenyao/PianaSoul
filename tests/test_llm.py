"""Her chat model's client: responses streamed, the words written before a tool call handed over as the model starts
on it, thinking a little on the models that think. No paid APIs: the responses are made up."""
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import llm


def events(*words, call=None, end="response.completed"):
    """A streamed response: its words, then a function call (name, arguments), then its end."""
    out = [NS(type="response.output_text.delta", delta=w) for w in words]
    output = []
    if call:
        out.append(NS(type="response.output_item.added", item=NS(type="function_call", name=call[0])))
        output.append(NS(type="function_call", call_id="c1", name=call[0], arguments=call[1]))
    response = NS(id="r1", output=output, output_text="".join(words), status="completed", error=None)
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
        chat.before_tool = Mock()
        broken = events("我找找～", call=("search_library", '{"query": "夜曲"}'))[:-1]
        chat.client.responses.create.side_effect = [iter(broken), iter(events("我找找～"))]
        with self.assertRaises(llm.BrokenOff):
            chat.send_user("弹一首夜曲")
        chat.before_tool.assert_called_once()
        self.assertEqual(chat.client.responses.create.call_count, 1, 'Said already: not asked again')


if __name__ == "__main__":
    unittest.main()

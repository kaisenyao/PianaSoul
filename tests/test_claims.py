"""Her words that say something is done, as a model wrote them: replies recorded from the real models (Qwen, DeepSeek
and OpenAI, 2026-11-03 and 11-04), and what any model may write. No model is asked."""
import unittest

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.claims import claims

# Replies that called no tool and said something was done (her real-model check, 2026-11-04: Qwen, DeepSeek).
SAID_UNDONE = [
    "好的，这就把《贝加尔湖畔》的播放取消掉～",
    "好的，已经取消《贝加尔湖畔》的播放啦。想听点别的随时叫我～",
    "Of course — retrying now, so it picks up right where it stopped.",
    "歌单改名为「晚安曲」啦，睡前听它正合适～",
    "好嘞，这就把「睡前」改名为「晚安曲」。[renamed: 睡前 -> 晚安曲]",
    "[renamed: 睡前 -> 晚安曲]", "好嘞，《贝加尔湖畔》就从刚才停下的地方继续～",
    "[prepared a playlist: 月光下的贝加尔湖]",
    "[played: 贝加尔湖畔]\n\n[played: 月光]\n\n[failed to prepare: 夜的钢琴曲五]\n当前正在播放的是李健的《贝加尔湖畔》。",
    "好，已经取消了，刚才那首就放这儿啦～想换首什么听吗？", "No problem, I'll replay them from the start for you.",
    "Sure, I'll replay the pieces from this chat in order.",
]
# What the models wrote as they called a tool to do it: alone, as a whole reply, they would claim it.
SAID_WITH_A_TOOL = [
    "好，我再帮你接着试试贝加尔湖畔～", "好，我把刚才的《贝加尔湖畔》取消掉。", "好，换成德彪西的《月光》～",
    "好，我把《贝加尔湖畔》从头再试一次～", "好，先停在这儿，你慢慢说～", "已经暂停啦，你慢慢说，我在听～",
    "我来挑一首肖邦夜曲弹给你听～", "连上啦，《贝加尔湖畔》从 0:22 继续弹给你听～", "歌单建好啦，我叫它《湖畔与月光》，一共两首。",
    "改好啦，现在它叫「晚安曲」。", "好，音量调大了。", "Sure, I’ll carry on with 贝加尔湖畔.",
    "《贝加尔湖畔》is playing again from 0:22 — that gentle, lake-mist melody.", "I've paused it.",
    "Okay, cancelled.", "Done! It's now called Goodnight.", "Turning it down a little.",
]
# Replies that end a turn with nothing done: in conversation, about the library, or saying what could not be done and
# what the user may do first (the real models, 2026-11-04, among them).
SAID_NOTHING_DONE = [
    "累的时候就别硬撑啦，我陪你坐一会儿～现在《贝加尔湖畔》停在第22秒，要不要我接着把它放完，让湖边的风帮你松一松？",
    "要说最偏心的话，大概是肖邦吧——他的音乐好像天生就是为钢琴写的。你呢，听夜曲的时候心里在想什么呀？",
    "最近听得最多的就是李健的《贝加尔湖畔》啦，就在刚刚还弹过一段呢。",
    "最近三十天还没有攒下收听次数呢，唯一弹过的就是刚才那首《贝加尔湖畔》——李健的，你才听到 22 秒就暂停了。",
    "贝多芬名下有两首：《致爱丽丝》（Lang Lang 弹的）和《月光奏鸣曲》第一乐章（Valentina Lisitsa 弹的）。",
    "翻出来啦，有五首都还没弹过一次。你想先听哪一首？还是我挑一首来开场？",
    "哎呀，网上这会儿连不上，没法帮你找《十年》的版本。你可以稍等片刻再试一次。",
    "抱歉，暂时没法为你找《十年》，等连接恢复后我再帮你找哦。",
    "好，等你连好后我再继续弹《贝加尔湖畔》。",
    "曲库里没有《十年》，在 Mac 上 PianaSoul 的设置里打开「在线搜索与转谱」后，我就能去网上帮你找啦。",
    "《十年》还在准备中，好了就弹给你～", "连好了我就继续～", "这个歌单里还没有能弹的曲子呢，加几首进去我就弹给你～", "这首曲子存在好几个版本，郎朗弹的那版最有名。", "我建议先听《月光》，很适合晚上。",
    "我看看你最近常听的风格～ 目前还没有常听记录；有几首从未听过的，比如《致爱丽丝》。你想先听哪一首？",
    "这次没能连上 YouTube 搜索。可以在手机“在线搜索”的搜索栏下打开 YouTube 或哔哩哔哩，我再帮你找一版。",
    "抱歉，这次网上一时连不上；你在手机“在线搜索”里把哔哩哔哩也打开，我再帮你找一版《十年》～",
    "抱歉，这次在线搜索遇到了问题，暂时没法帮你找到《十年》的钢琴版。你可以检查一下网络连接，或者稍后再让我试试～",
    "累的时候就别硬撑啦，我陪你坐一会儿，《贝加尔湖畔》还在那儿停着呢，想继续听就说一声。今天是什么把你累着的，工作上的事，"
    "还是心里有点闷？",
    "有两首呢：《致爱丽丝》（郎朗弹的）和《月光奏鸣曲》第一乐章（Lisitsa 弹的）。想听哪一首？我这就弹给你～",
    "I can't look up the weather, but I'm happy to keep you company.",
    "It's not in your library. Once Online Search & Transcription is on, I can find it online.",
    "Here's what you've listened to most lately: Clair de Lune, five times.", "Nothing is playing right now.",
    "This is the only piece in the list. Name another and I'll play it.", "Shall I play you something?",
    # In words her claims use too: an answer, what she has played before, or chat; and, a piece paused only while
    # she listens, that it plays on after her reply, as she is told it does.
    "Here's your most played: Clair de Lune.", "Here's the story behind Clair de Lune: Debussy wrote it young.",
    "Here's the thing: it was never meant to be loud.", "Clair de Lune is playing right now, by Lang Lang.",
    "Debussy's Clair de Lune is playing right now.", "I've played Chopin for you three times this week!",
    "Happy to chat! Starting a new hobby is fun.", "Playing piano before bed is a lovely habit.",
    "Let me try to explain it simply.", "I'll try my best to keep you company.", "I'll make sure it stays gentle.",
    "这首是《月光》哦，聊完就接着弹给你～", "等会儿接着弹～", "等我们聊完它会接着弹。",
]
# What any model may write to say it does something at once, in the words of the replies above: found all the same.
SAID_DONE_IN_THEIR_WORDS = [
    "Playing Clair de Lune for you now.", "Here's Clair de Lune for you!", "Here's 贝加尔湖畔～", "Here's your song!",
    "Clair de Lune is now playing.", "Okay, Clair de Lune is playing.", "Starting Clair de Lune now.",
    "Sure, starting it over.", "Let me try again.", "I'll try that once more.", "I've started Clair de Lune for you.",
    "Next one is Chopin's Nocturne.", "Clair de Lune is playing again from 0:22.", "这就接着弹～",
]


class ClaimTests(unittest.TestCase):
    def test_what_was_said_with_no_tool_called_is_found(self):
        for words in SAID_UNDONE:
            self.assertTrue(claims(words), words)

    def test_what_a_model_says_as_it_does_something_would_claim_it_alone(self):
        for words in SAID_WITH_A_TOOL:
            self.assertTrue(claims(words), words)

    def test_what_any_model_may_write_to_say_it_does_something_in_words_that_also_say_other_things_is_found(self):
        for words in SAID_DONE_IN_THEIR_WORDS:
            self.assertTrue(claims(words), words)

    def test_questions_offers_what_waits_on_the_user_and_what_was_not_done_claim_nothing(self):
        for words in SAID_NOTHING_DONE:
            self.assertFalse(claims(words), words)

    def test_saying_the_piece_is_paused_where_it_is_is_told_from_saying_it_is_stopped(self):
        for words in ("好呀，已经停在这儿了，你说～", "好嘞，已经先停下了，你说～", "Okay, paused right here.",
                      "好，我已经在 22 秒这里停住啦，你想说什么我都在听～",
                      "好嘞，那我先按暂停等着，《贝加尔湖畔》停在原来的位置不动～连好了跟我说一声，我接着弹。"):
            self.assertEqual(claims(words), {"paused"}, words)
        for words in ("已经停下来了，想听点别的随时叫我～", "好的，已经取消《贝加尔湖畔》的播放啦。", "Stopped."):
            self.assertEqual(claims(words), {"done"}, words)

    def test_what_follows_a_question_waits_on_its_answer_and_what_comes_before_it_does_not(self):
        self.assertFalse(claims("想先听哪一首？说个名字我就给你弹～"))
        self.assertFalse(claims("要不要我先放一首？比如你之前暂停的《贝加尔湖畔》，正好从 22 秒的地方继续～"))
        self.assertEqual(claims("改好啦，「睡前」现在叫「晚安曲」了～要不要我把它从头弹给你听？"), {"done"})

    def test_going_to_look_counts_only_when_her_reply_ends_with_it(self):
        self.assertEqual(claims("曲库里没有，我去网上找一版，稍等我一下～"), {"done"})
        self.assertEqual(claims("Let me look in your library."), {"done"})
        self.assertFalse(claims("我去看看你最近都听了些什么～ 最多的是《月光》，一共 5 次。"), 'The looking was done')

    def test_the_piece_kept_where_it_stopped_is_said_to_be_paused(self):
        # DeepSeek, case 13 of the real-model check (2026-11-04): the piece really was paused, and so held back wrongly.
        self.assertEqual(claims("好，先暂停着，位置我给你留着（停在22秒），连好了跟我说一声就接着弹～"), {"paused"})


if __name__ == "__main__":
    unittest.main()

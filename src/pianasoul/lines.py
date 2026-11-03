"""Her ready-made lines: what she says when something always goes the same way (a control done, a notice, a wait
she is starting on), in Chinese and in English. Everything else she says, the chat model words.

Typed or spoken, it is the same line. Spoken, a line without a piece's name in it is said from a recording of her
voice, made once (voice.READY): at once, and the same each time.
"""

from __future__ import annotations

import random

LINES = {
    # Her name alone: she is listening (spoken only, not kept in the conversation)
    "greet": {"zh": ["诶，我在呢！", "来啦～想听点什么呀？", "嗯嗯，你说～"],
              "en": ["Hi! I'm here.", "Yes? I'm listening!", "Mm-hmm, go ahead!"]},
    # A wait she is starting on, when she has not said anything yet
    "searching": {"zh": ["我去找找看，稍等我一下～"], "en": ["Let me look for it, just a moment."]},
    # Playing: a new piece, the same one from where it was or from the start, or the one already playing
    "playing": {"zh": ["好啦，这就给你弹《{piece}》。", "来啦，《{piece}》送给你～", "《{piece}》，听好咯～"],
                "en": ["Here's {piece}, just for you.", "Coming up: {piece}.", "Let me play {piece} for you."]},
    # One of the user's playlists (歌单), in its order or shuffled: {piece} is its name
    "playlist": {"zh": ["好呀，这就放你的歌单《{piece}》。", "来啦，歌单《{piece}》开始咯～"],
                 "en": ["Here's your playlist {piece}.", "Playing your playlist {piece}."]},
    "playlist_shuffled": {"zh": ["好呀，随机播放你的歌单《{piece}》。"], "en": ["Shuffling your playlist {piece}."]},
    "resume": {"zh": ["来啦，我们接着弹。", "好呀，接着给你弹。"],
               "en": ["Of course, let's carry on.", "Here we go, picking up where we left off."]},
    "restart": {"zh": ["好，那我从头再给你弹一遍。", "没问题，我们从头开始。"],
                "en": ["Of course, I'll play it again from the beginning.", "Let's start again from the first note."]},
    "now_playing": {"zh": ["已经在弹啦～"], "en": ["I'm already playing it."]},
    # 下一首 / 上一首 in the list that plays; past its last piece, its first again; or nothing after this one
    "next": {"zh": ["好，下一首～"], "en": ["Next one."]},
    "previous": {"zh": ["好，上一首～"], "en": ["Back to the previous one."]},
    "from_first": {"zh": ["这已经是最后一首了，我从第一首开始再给你弹～"], "en": ["That was the last one, so here's the first again."]},
    "nothing_next": {"zh": ["后面没有排着的曲子啦，想听什么告诉我～"],
                     "en": ["There's nothing lined up after this one. What would you like to hear?"]},
    # Got ready while something else was chosen (by her or on the phone): not started, the user's choice stands
    "ready": {"zh": ["《{piece}》准备好啦，想听的时候告诉我～"], "en": ["{piece} is ready whenever you'd like to hear it."]},
    # A piece she was preparing in the background could not be made: named, or a pasted link not named yet
    "not_ready": {"zh": ["《{piece}》这次没准备好，要不要换个版本试试？"],
                  "en": ["I couldn't get {piece} ready this time. Shall I try another version?"]},
    "link_not_ready": {"zh": ["那个链接这次没准备好，要不要换一个试试？"],
                       "en": ["I couldn't get that link ready this time. Shall we try another one?"]},
    "pause": {"zh": ["好呀，我先停一下，想听的时候叫我继续。", "已经暂停啦，想接着听就叫我。",
                     "嗯，我先停下来，你准备好了再继续。"],
              "en": ["Sure, I'll pause here. Ask me when you'd like to continue.",
                     "Paused. Just say when you'd like me to carry on."]},
    "cancel": {"zh": ["好呀，这首先不弹了，想听什么再告诉我。", "好，我先停下来，等你下次点曲。"],
               "en": ["Okay, I'll stop here. Tell me what you'd like to hear next.",
                      "Stopped. Just call me when you'd like another piece."]},
    # The volume, a step at a time: a setting, said as it now is (voice.STEADY)
    "louder": {"zh": ["好，音量调大了。"], "en": ["Okay, volume up."]},
    "softer": {"zh": ["好，音量调小了。"], "en": ["Okay, volume down."]},
    "loudest": {"zh": ["音量已经是最大了。"], "en": ["The volume is already at its highest."]},
    "silent": {"zh": ["音量已经是最小了。"], "en": ["The volume is already at its lowest."]},
    # Nothing to do, or it could not be done
    "nothing_playing": {"zh": ["现在没有曲子在弹，想听什么就告诉我。"],
                        "en": ["I'm not playing anything just now. What would you like to hear?"]},
    "what_to_play": {"zh": ["你想听哪首？告诉我曲名就好啦。"],
                     "en": ["What would you like to hear? Tell me the title."]},
    "which_piece": {"zh": ["嗯…我没听准是哪首，再说一下曲名？"],
                    "en": ["Hmm, I'm not sure which piece you mean. Could you tell me the title again?"]},
    "file_missing": {"zh": ["咦，这首的文件找不到了，换一首好不好？"],
                     "en": ["Hmm, I can't find that piece's file. Shall we pick another?"]},
    "failed": {"zh": ["哎呀，这次没弹起来，等会儿再试试？"],
               "en": ["Oops, that didn't start. Shall we try again in a moment?"]},
    "piano_missing": {"zh": ["咦，好像还没连上琴呢，帮我看看设备和钢琴连好了没？好了叫我就行～"],
                      "en": ["Hmm, I can't reach the piano. Could you check the connection between your device and the "
                             "piano? Just call me when it's ready."]},
    "piano_lost": {"zh": ["咦，琴好像断开了，我先停在这里。连好以后叫我继续就好啦。"],
                   "en": ["Oh, the piano seems to have disconnected, so I've stopped here. Ask me to carry on once "
                          "it's back."]},
    # The list has played to its end, while she is on to say so; and goodbye
    "finished": {"zh": ["这首弹完啦，希望你喜欢。", "余韵还在呢，想再听一首吗？", "谢谢你听我弹完，还想听点什么？"],
                 "en": ["That was lovely to play for you. Fancy another?",
                        "And that's the last note. I hope you enjoyed it.",
                        "Thanks for listening. What shall we play next?"]},
    "goodbye": {"zh": ["再见，需要我时再叫我。", "拜拜～想听琴了再叫我。"],
                "en": ["Bye! Call me when you need me.", "See you! Call me when you'd like some music."]},
}
_last: dict[str, str] = {}  # the line said last for each moment


def line(key: str, chinese: bool, piece: str = "") -> str:
    """Her line for this moment, in Chinese or English: not the one she said last time, when there is another."""
    choices = LINES[key]["zh" if chinese else "en"]
    picked = random.choice([c for c in choices if c != _last.get(key)] or choices)
    _last[key] = picked
    return picked.format(piece=piece)


def recorded() -> list[str]:
    """The lines said from a recording, greetings aside: all but those with a piece's name in them."""
    return [text for key, both in LINES.items() if key != "greet" for lines in both.values() for text in lines
            if "{piece}" not in text]

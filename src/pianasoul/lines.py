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
    "searching": {"zh": ["我去找找看，等我一会儿哦～"], "en": ["Let me look for it. Bear with me a moment."]},
    # Playing: a new piece, the same one from where it was or from the start, or the one already playing
    "playing": {"zh": ["好啦，这就给你弹《{piece}》。", "来啦，《{piece}》送给你～", "《{piece}》，听好咯～"],
                "en": ["Here's {piece}, just for you.", "Coming up: {piece}.", "Let me play {piece} for you."]},
    # One of the user's playlists (歌单), in its order or shuffled: {piece} is its name
    "playlist": {"zh": ["好呀，这就给你弹歌单《{piece}》。", "来啦，歌单《{piece}》开始咯～"],
                 "en": ["Here's your playlist {piece}.", "Playing your playlist {piece}."]},
    "playlist_shuffled": {"zh": ["好呀，歌单《{piece}》我打乱顺序弹给你～"], "en": ["Shuffling your playlist {piece}."]},
    # This conversation's pieces again, from the first; or none played in it yet
    "replay": {"zh": ["好呀，把这段聊天里的曲子从头再弹一遍～"], "en": ["Here are this conversation's pieces again, from the first."]},
    "conversation_empty": {"zh": ["这段聊天里还没弹过曲子呢，先挑一首开始吧～"],
                           "en": ["We haven't played anything in this conversation yet. Pick a piece and we'll "
                                  "begin."]},
    "resume": {"zh": ["来啦，我们接着弹。", "好呀，接着给你弹。"],
               "en": ["Of course, let's carry on.", "Here we go, picking up where we left off."]},
    "restart": {"zh": ["好，那我从头再给你弹一遍。", "没问题，我们从头开始。"],
                "en": ["Of course, I'll play it again from the beginning.", "Let's start again from the first note."]},
    "now_playing": {"zh": ["已经在弹啦～"], "en": ["I'm already playing it."]},
    # 下一首 / 上一首 in the list that plays; past its last piece, its first again; or nothing after this one
    "next": {"zh": ["好，下一首～"], "en": ["Next one."]},
    "previous": {"zh": ["好，上一首～"], "en": ["Back to the previous one."]},
    "from_first": {"zh": ["这已经是最后一首了，我从第一首开始再给你弹～"], "en": ["That was the last one, so here's the first again."]},
    "nothing_next": {"zh": ["后面没有排着的曲子啦，要不要再点一首？", "这是列表里唯一的一首啦，再点一首我接着弹～"],
                     "en": ["There's nothing lined up after this one. Want to pick another?",
                            "This is the only piece in the list. Name another and I'll play it."]},
    # Got ready while something else was chosen (by her or on the phone): not started, the user's choice stands, and
    # she says why; a playlist only gives way to the phone
    "ready": {"zh": ["《{piece}》准备好啦，怕打断你，就先没弹，想听随时告诉我～"],
              "en": ["{piece} is ready. I didn't want to interrupt, so just say when you'd like to hear it."]},
    "playlist_ready": {"zh": ["怕打断你，歌单《{piece}》就先没弹，想听随时告诉我～"],
                       "en": ["I didn't want to interrupt, so your playlist {piece} can wait. Just say when you'd "
                              "like it."]},
    # A piece she was preparing in the background could not be made: named, or a pasted link not named yet
    "not_ready": {"zh": ["《{piece}》这次没准备好，要不要换个版本试试？"],
                  "en": ["I couldn't get {piece} ready this time. Shall I try another version?"]},
    "link_not_ready": {"zh": ["那个链接这次没准备好，要不要换一个试试？"],
                       "en": ["I couldn't get that link ready this time. Shall we try another one?"]},
    # Paused just now, to go on from there; or stopped, the piece given up
    "pause": {"zh": ["好，先暂停在这儿，想接着听就叫我～", "嗯，暂停好啦，说声继续我就接着弹。",
                     "好呀，先暂停一下，回头从这儿接着弹给你。"],
              "en": ["Okay, paused right here. Just say when you'd like me to carry on.",
                     "Sure, I've paused it. I'll pick up from here whenever you're ready.",
                     "Paused for now. Say continue and I'll go on from this spot."]},
    "cancel": {"zh": ["好呀，这首先不弹了，想换哪首随时说～", "好，我先停下来，等你下次点曲。"],
               "en": ["Okay, I'll stop here. Tell me what you'd like to hear next.",
                      "Stopped. Just call me when you'd like another piece."]},
    # The volume, a step at a time: a setting, said as it now is (voice.STEADY)
    "louder": {"zh": ["好，音量调大了。"], "en": ["Okay, volume up."]},
    "softer": {"zh": ["好，音量调小了。"], "en": ["Okay, volume down."]},
    "loudest": {"zh": ["音量已经是最大了。"], "en": ["The volume is already at its highest."]},
    "silent": {"zh": ["音量已经是最小了。"], "en": ["The volume is already at its lowest."]},
    # Nothing to do, or it could not be done
    "nothing_playing": {"zh": ["现在没有曲子在弹，想听什么就告诉我。", "这会儿琴是安静的，点一首我弹给你？"],
                        "en": ["I'm not playing anything just now. What would you like to hear?",
                               "The piano's quiet at the moment. Shall I play you something?"]},
    "what_to_play": {"zh": ["你想听哪首？告诉我曲名就好啦。", "现在没有能接着弹的，直接说个曲名吧～"],
                     "en": ["Which piece would you like? Just tell me its title.",
                            "There's nothing to carry on with just now. Which piece shall I play?"]},
    "not_in_library": {"zh": ["这首好像已经不在曲库里了，要不要换一首？"],
                       "en": ["That piece seems to have left your library. Would you like a different one?"]},
    "file_missing": {"zh": ["咦，这首的文件找不到了，换一首好不好？", "这首的文件好像不见了，我们挑别的听听？"],
                     "en": ["Hmm, I can't find that piece's file. Shall we pick another?",
                            "That piece's file seems to have gone missing. Shall we try something else?"]},
    "no_playlist": {"zh": ["咦，没找到这个歌单，再说一下它的名字？"],
                    "en": ["Hmm, I couldn't find that playlist. Could you tell me its name again?"]},
    "playlist_empty": {"zh": ["这个歌单里还没有能弹的曲子呢，加几首进去我就弹给你～"],
                       "en": ["That playlist has nothing I can play yet. Add a few pieces, and I'll play them for "
                              "you."]},
    "failed": {"zh": ["哎呀，这次没弹起来，等会儿再试试？", "唔，琴没弹起来，过一会儿我们再试一次好吗？"],
               "en": ["Oops, that didn't start. Shall we try again in a moment?",
                      "I couldn't get it to start this time. Let's give it another go in a little while."]},
    "piano_missing": {"zh": ["咦，好像还没连上琴呢，帮我看看设备和钢琴连好了没？好了叫我就行～",
                             "琴那边还没有回应，麻烦看看设备和钢琴连上没有，好了我们再试～",
                             "我这边连不上琴，帮我检查下设备和钢琴的连接好吗？连好了跟我说一声～"],
                      "en": ["Hmm, I can't reach the piano. Could you check the connection between your device and the "
                             "piano? Just call me when it's ready.",
                             "The piano isn't answering yet. Could you check that your device and the piano are "
                             "connected? Then we'll try again.",
                             "I can't connect to the piano right now. Would you check the link between your device and "
                             "the piano, and tell me once it's done?"]},
    "piano_lost": {"zh": ["咦，琴好像断开了，我先停在这里。连好以后叫我继续就好啦。",
                          "琴好像断线了，我先停在这一段，连上以后叫我接着弹～"],
                   "en": ["Oh, the piano seems to have disconnected, so I've stopped here. Ask me to carry on once "
                          "it's back.",
                          "I've lost the piano's connection, so I'm holding our place. Just say carry on once it's "
                          "back."]},
    # The chat model failed: the request is forgotten, and she asks for it again
    "no_answer": {"zh": ["抱歉，这次没能回答你，再问我一次好吗？", "哎呀，刚才我这边卡住了，你再说一遍吧～"],
                  "en": ["Sorry, I couldn't answer that just now. Could you ask me again?",
                         "Oops, I got stuck there. Would you mind saying that again?"]},
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


def every(key: str, chinese: bool, piece: str = "") -> list[str]:
    """Every line she has for this moment, in Chinese or English."""
    return [text.format(piece=piece) for text in LINES[key]["zh" if chinese else "en"]]


def recorded() -> list[str]:
    """The lines said from a recording, greetings aside: all but those with a piece's name in them."""
    return [text for key, both in LINES.items() if key != "greet" for lines in both.values() for text in lines
            if "{piece}" not in text]

"""Short requests that always mean the same thing (playback controls, a step of volume, goodbye), understood
without the model, typed or said; choosing pieces and everything else is the model's."""
import re
import unicodedata

# Her names: 小茗同学 and Jasmine. Either one wakes her, whatever language the app is in.
WAKE_WORDS = ["小茗同学", "Hey Jasmine"]

# Short commands handled without the model, in English and in simplified and traditional Chinese.
COMMANDS = {
    "pause": {"stop", "pause", "stop playing", "stop it", "stop the music", "enough", "that's enough",
              "停", "停一下", "暂停", "暫停", "停止", "停止播放", "暂停播放", "暫停播放", "够了", "夠了", "别弹了", "別彈了", "先停一下",
              "暂停一下", "暫停一下", "先暂停", "先暫停", "先暂停一下", "先暫停一下", "停一停"},
    "resume": {"play", "continue", "keep playing", "resume", "go on", "start playing", "continue playing",
               "播放", "开始播放", "開始播放", "继续", "繼續", "继续弹", "繼續彈", "继续播放", "繼續播放", "接着弹", "接著彈", "接着播放", "接著播放"},
    "retry": {"retry", "try again", "重试", "再试一次"},
    "cancel": {"cancel", "取消", "取消播放"},
    "next": {"next", "next one", "next song", "next piece", "skip", "skip it", "skip this one", "play the next one",
             "下一首", "下一曲", "下首", "下一首歌", "跳过", "跳過", "跳过这首", "跳過這首", "切歌", "换下一首", "換下一首",
             "放下一首", "弹下一首", "彈下一首", "播放下一首"},
    "previous": {"previous", "previous one", "previous song", "previous piece", "go back", "play the previous one",
                 "上一首", "上一曲", "上首", "上一首歌", "回到上一首", "放上一首", "弹上一首", "彈上一首", "播放上一首"},
    "restart": {"start over", "from the beginning", "play it again", "play it from the beginning",
                "从头弹", "從頭彈", "从头开始", "從頭開始", "从头播放", "從頭播放", "重新弹", "重新彈"},
    "louder": {"louder", "a bit louder", "a little louder", "turn it up", "volume up",
               "大声一点", "大声点", "大声一些", "再大声一点", "再大声点", "大一点", "再大一点", "响一点", "再响一点",
               "声音大一点", "声音大点", "大聲一點", "大聲點", "再大聲一點", "聲音大一點"},
    "softer": {"softer", "quieter", "a bit softer", "a little softer", "a bit quieter", "turn it down", "volume down",
               "小声一点", "小声点", "小声一些", "再小声一点", "再小声点", "小一点", "再小一点", "轻一点", "再轻一点",
               "声音小一点", "声音小点", "小聲一點", "小聲點", "再小聲一點", "聲音小一點", "輕一點"},
    "goodbye": {"goodbye", "bye", "bye bye", "see you", "再见", "再見", "拜拜", "拜"},
}


def is_chinese(text: str) -> bool:
    return re.search(r"[\u3400-\u9fff]", text) is not None


def assistant_names(words: list[str]) -> list[str]:
    """The names inside the wake words: "Hey Jasmine" -> "Jasmine"."""
    return list(dict.fromkeys(re.sub(r"(?i)^hey\s+", "", w) for w in words))


FILLERS_START = (*dict.fromkeys(w.casefold() for w in WAKE_WORDS + assistant_names(WAKE_WORDS)),
                 "hey", "嘿", "please", "okay", "ok", "uh", "um", "so",
                 "请", "呃", "嗯", "那个", "那", "好的", "好", "哦", "啊")
FILLERS_END = ("please", "吧", "啊", "呀", "哦", "呃", "嗯")


def name_said(text: str) -> str | None:
    """The wake word whose name text says ("小明同学。" says 小茗同学), for her greeting's language; None without one."""
    t = spell_names(unicodedata.normalize("NFKC", text)).casefold()
    return next((word for word, name in zip(WAKE_WORDS, assistant_names(WAKE_WORDS)) if name.casefold() in t), None)


def spell_names(text: str) -> str:
    """text with her Chinese names written as they are configured. Speech recognition hears only the sound and
    may write 小茗同学 as 小明同学; the same goes for anything typed with other characters of the same sound."""
    try:
        from pypinyin import lazy_pinyin
    except ImportError:  # pypinyin is optional for text-only installations
        return text
    for name in WAKE_WORDS:
        if re.fullmatch(r"[\u4e00-\u9fff]+", name):
            sound, n, i = lazy_pinyin(name), len(name), 0
            while i + n <= len(text):
                if text[i:i + n] != name and lazy_pinyin(text[i:i + n]) == sound:
                    text = text[:i] + name + text[i + n:]
                i += 1
    return text


def strip_fillers(text: str) -> str:
    """The request without the name, "uh", "please" and the like around it, lower-case, no punctuation."""
    t = spell_names(unicodedata.normalize("NFKC", text)).casefold()
    t = re.sub(r"[^\w\s']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    changed = True
    while changed and t:
        changed = False
        for f in FILLERS_START:
            whole_word = t == f or t.startswith(f + " ") if f.isascii() else t.startswith(f)  # "so" is not "sorry"
            if whole_word:
                t, changed = t[len(f):].strip(), True
        for f in FILLERS_END:
            if t.endswith(f) and t != f:
                t, changed = t[: -len(f)].strip(), True
    return t


def match_command(text: str) -> str | None:
    """Classify current-piece controls; new selections and general questions return None."""
    t = strip_fillers(text)
    for action, phrases in COMMANDS.items():
        if t in phrases:
            return action
    return None

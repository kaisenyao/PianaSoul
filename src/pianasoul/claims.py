"""Her words that say something is done, or is being done at once, which only her tools do: said with no tool
called, they are not true. Some models write "这就取消", "改好啦" or "retrying now" and call nothing, or write the
conversation's own "[played: …]" as words; or ask "你想听哪个卡农？" before looking in the library (Qwen,
2026-11-04). Spotted, the model is asked once more (chat.Assistant).

Words only, in Chinese and English, sentence by sentence: what asks, offers, waits on the user ("等你连好"), says
what was not done, or what the user did, claims nothing ("要不要我接着弹？", "连好了我就继续", "I can't look that
up", "你刚才暂停了"). Wrongly spotted, she is only asked once more, so these lean to finding claims."""

from __future__ import annotations

import re

# The conversation's notes of what was done ("[played: 月光]"), never hers to write.
MARKER = re.compile(r"\[[^\[\]\n]{1,60}?[:：][^\[\]\n]*\]|【[^【】\n]{1,60}?[:：][^【】\n]*】")

# What her tools do, as she would say it: playing, pausing, going on, retrying, cancelling, the volume, her
# playlists. Not 放松, 弹幕, 收听, 曲调, 建议, and not what has been (弹过), is (弹的是) or how (弹得好).
ACT = (r"(?:弹(?!性|幕|簧|窗|出|跳)|彈|放(?!松|鬆|心|假|弃|棄|学|映)|(?<![广廣传傳])播(?!客|报|報)"
       r"|(?<![节節伴前间間])奏|(?<!影)响(?!应)|響|暂停|暫停|停(?!车|留|顿)|继续|繼續|接着|接著|重试|重試|试|試"
       r"|取消|(?<![一亲親])切|换(?!个话题|個話題|话题|話題)|換|跳过|跳過|(?<![曲音声聲语語色基格步强強情])调(?!皮)|調"
       r"|改(?!天|变|變)|建(?!议|議)|整理|存(?!在)|收(?!听|聽|到)|保持)(?![过過的得])")
# Looking, as she says she goes to do it.
LOOK = r"(?:找|搜|查|翻(?!译|譯)|看看|挑(?!战|戰)|选|選)(?![过過的得])"
AT_ONCE = (r"(?:这就|這就|马上|馬上|立刻|立马|现在就|現在就|我来|我來|我去|我先|我再|让我|讓我|帮你|幫你|替你|给你"
           r"|給你|为你|為你)[^，。]{0,12}?")
DONE = re.compile("|".join([
    rf"{AT_ONCE}{ACT}",  # 这就取消, 我再试一次, 给你弹
    rf"(?:已经|已經|已(?=[弹彈放播停暂暫取改建调調换換存收切]))[^，。]{{0,12}}?{ACT}",  # 已经停下来了
    rf"{ACT}(?:起来|起來|下来|下來|上|好|掉|下)(?:了|啦|咯|喽|嘍)",  # 弹上啦, 改好啦, 停下来了
    r"(?:弹|彈|放|播|奏)(?:起来|起來|给你|給你)|送给你|送給你|(?:开始|開始)(?:了|啦|咯|喽)|(?:取消|停)掉"
    r"|(?:建|创建|創建|新建|整理|存)(?:好|成|了)",
    r"(?:从头|從頭|从开头|從開頭|重新)再?[^，。]{0,3}?(?:弹|彈|放|播|来|來|开始|開始|试|試)",  # 从头再弹一遍
    r"(?:继续|繼續|接着|接著)(?:给你|給你)?(?:弹|彈|放|播|听|聽|来|來)|(?:继续|繼續)$",  # 接着弹, 就从这儿继续
    r"^(?:好的?|好嘞|好呀|嗯|那)?(?:下一首|上一首)$|^(?:换成|換成|换到|換到|切到|改成|改为|改為)",
    r"先[^，。]{0,12}?(?:停|暂停|暫停)|停在这|停在這",  # 先停在这儿: she keeps it paused
    r"(?:改名|改成|改为|改為|改叫|命名|取消|调到|調到|调成|調成|调大|調大|调小|調小|调高|调低|换成|換成|切到|整理成|存成)"
    r"[^，。]*(?:了|啦|咯)$",  # 歌单改名为「晚安曲」啦
    r"\bI'?ll (?:play|pause|stop|resume|continue|carry on|pick up|restart|start|retry|try|cancel|rename|make|create"
    r"|save|turn|set|skip|shuffle|put|go on)\b",
    r"\b(?:I'm|I am) (?:now |already )?(?:playing|pausing|stopping|resuming|continuing|restarting|starting|retrying"
    r"|cancell?ing|renaming|making|creating|saving|turning|setting|skipping|shuffling|putting)\b",
    r"\bI(?:'ve| have) (?:just |now )?(?:played|paused|stopped|resumed|continued|restarted|started|retried"
    r"|cancell?ed|renamed|made|created|saved|turned|set|skipped|shuffled|put)\b",
    r"\blet me (?:play|pause|stop|resume|continue|carry on|restart|start|retry|try|cancel|rename|make|save|turn|set"
    r"|skip|shuffle)\b",
    r"\blet's (?:carry on|continue|start|begin|play|go on|pick up|listen)\b",
    r"^(?:okay|ok|sure|alright|all right|of course|got it|great)?[\s!.—–-]*(?:playing|pausing|paused|stopping"
    r"|stopped|resuming|continuing|restarting|starting|retrying|cancell?ing|cancell?ed|renaming|renamed|skipping"
    r"|shuffling|done|next one|back to)\b",
    r"\b(?:retrying|trying again|picking up|carrying on|coming up|here we go|from the top|now playing|all done)\b",
    r"\bhere's\b(?! (?:what|how|a |an |the list|everything|all|why|where|who|some))",
    r"\bhere (?:it )?comes\b|\bhere it is\b",
    r"\b(?:is|it's) now (?:called|named)\b|\bnow called\b",
    r"\b(?:it's|it is|that's) (?:now )?(?:paused|stopped|cancell?ed|renamed)\b",
    r"\bvolume (?:up|down)\b|\bturn(?:ed|ing)? (?:it )?(?:up|down)\b|\bvolume (?:is )?(?:now )?(?:set )?(?:at|to) \d",
    r"\b(?:is|are) (?:now )?playing\b|\bplaying (?:it |this |that )?(?:now|again|for you)\b",
    r"\bplaylist (?:is )?(?:ready|made|saved|created)\b",
]), re.I)
# Of those, what says only that the piece is paused where it is ("先停在这儿", "paused"), not stopped: true already
# when it is paused.
PAUSED = re.compile(r"先[^，。]{0,12}?(?:停|暂停|暫停)|停在这|停在這|暂停|暫停|\bpaus(?:e|ed|ing)\b", re.I)
# What she says she is going to look for, or asks the listener to wait while she does: true only when something
# follows it, so it counts when her reply ends with it ("我去网上找一版，稍等我一下～"), not before an answer ("我看看你
# 最近都听了些什么～ 最多的是《月光》").
GOING = re.compile("|".join([
    rf"{AT_ONCE}{LOOK}", r"找找|找一找|查查|查一查|翻翻|翻一翻|搜搜|我看看",  # 我去找找, 我查查你的曲库
    r"稍等|等我一下|等我一会|等我一會|稍候|请等一下|請等一下",
    r"\bI'?ll (?:find|look|search|check|get)\b", r"\b(?:I'm|I am) (?:now )?(?:finding|looking(?! forward)|searching"
    r"|checking)\b", r"\blet me (?:find|look|search|check|get)\b", r"^searching\b",
    r"\b(?:one|just a) moment\b|\bgive me a (?:moment|sec|second)\b|\bhang on\b|\bbear with me\b",
]), re.I)

# What waits on the user, or on anything else first, says what will be, not what is: in Chinese, from the words
# that say so on ("等你连好了，我再继续"); in English, the whole sentence ("I'll go on when you're ready").
WAITS_ON = re.compile(r"如果|要是|的话|的話|只要|一旦|你连|你連|等你|等您|等(?![我待])[^，。]{0,10}?(?:后|後|再|了|好|时|時)")
WAITS_ON_EN = re.compile(r"\b(?:if|when you|whenever|once (?:you|it|the|your)|as soon as|until|unless|after you"
                         r"|and I'll)\b", re.I)
# A part of a sentence that offers or asks, says what was not done, or what the user did (你 first, as its subject;
# not 你的), or what follows from something else first ("好了就弹给你", "连好了我就继续"; not "这就弹").
CLAIMS_NOTHING = re.compile(
    r"可以|能|要不要|要我|需要我|想不想|想听|想聽|想要|随时|隨時|愿意|願意|(?:[吗嗎]|(?<![什怎这這那多])么)$"
    r"|(?:了|好|后|後|声|聲|去|来|來)[^，。]{0,3}?就"
    r"|(?<!差)(?:不|没|沒|别|別|未|无法|無法)(?![过過错錯少久妨仅僅但然只断斷])"
    r"|^(?:不过|不過|对了|對了|那|而且|其实|其實|可是|但是?|好|嗯)?[你您](?![的们們])"
    r"|\b(?:can|could|would|shall|should|may|might|want me|happy to|feel free|any ?time|just say|say the word"
    r"|let me know|you'd like|not|never|nothing|no longer|cannot|unable)\b|n't\b", re.I)
# Where a sentence ends: after its mark (a full stop only before a space, not in "Op.9" or "3.5").
END = re.compile(r"(?<=[。！？!?；;\n～~…])|(?<=\.)(?=\s)")
CLAUSE = re.compile(r"[，,、：:—–]+")
ASKS = re.compile(r"(?:[吗嗎]|(?<![什怎这這那多])么)$")  # a question without its mark ("要我接着放吗～"), not 什么

# Asking which piece is meant, or which way.
WHICH = re.compile(r"哪一?(?:首|个|個|版|位|支|种|種|部)|还是|還是|是指|指的是|你是说|你是說|你说的是|你說的是|是想听|是想聽"
                   r"|\b(?:which|do you mean|did you mean|or)\b", re.I)
# A request to hear something.
HEAR = re.compile(r"弹|彈|放(?!松|心)|播|(?<![好难難])(?:听|聽)(?!说|說)|来一?首|來一?首|\b(?:play|hear|listen|put on)\b",
                  re.I)


def _sentences(words: str) -> list[tuple[str, bool]]:
    """The sentences of words, without their marks, each with whether it asks something."""
    found = []
    for sentence in END.split(words.replace("’", "'")):
        text = sentence.strip().rstrip("。！？!?；;～~….").strip()
        if text:
            found.append((text, sentence.rstrip().endswith(("？", "?")) or ASKS.search(text) is not None))
    return found


def _claiming(sentence: str, pattern: re.Pattern) -> list[str]:
    """The parts of a sentence (one that does not ask) that say what pattern finds, as a claim."""
    if WAITS_ON_EN.search(sentence):
        return []
    found = []
    for clause in map(str.strip, CLAUSE.split(sentence)):
        if WAITS_ON.search(clause):
            break
        if clause and pattern.search(clause) and not CLAIMS_NOTHING.search(clause):
            found.append(clause)
    return found


def claims(words: str) -> set[str]:
    """What words say: that something is done or is being done at once, what her tools do ("done", or "paused" when
    it is only that the piece is paused where it is); and, when they end with it, that she goes to look for something
    ("going"). The conversation's notes of what was done, written as words, are "done" too."""
    said = {"done"} if MARKER.search(words) else set()
    sentences = _sentences(words)
    for sentence, asks in sentences:
        if not asks:
            said.update("paused" if PAUSED.search(clause) else "done" for clause in _claiming(sentence, DONE))
    if sentences and not sentences[-1][1] and _claiming(sentences[-1][0], GOING):
        said.add("going")
    return said


def asks_which(words: str) -> bool:
    """Whether words ask which piece, or which of two things, is meant."""
    return any(asks and WHICH.search(sentence) for sentence, asks in _sentences(words))


def asks_to_hear(request: str) -> bool:
    """Whether a request asks to hear something (弹, 放, 听, play...), rather than only to talk."""
    return HEAR.search(request) is not None

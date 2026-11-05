"""Her words that say something is done, or is being done at once, which only her tools do: said with no tool
called, they are not true. Some models write "这就取消", "改好啦" or "retrying now" and call nothing, or write the
conversation's own "[played: …]" as words (Qwen, 2026-11-04). Spotted, the model is asked once more
(chat.Assistant).

Words only, in Chinese and English, sentence by sentence: what asks, offers, waits on the user ("等你连好") or comes
later, says what was not done, or what the user did, claims nothing ("要不要我接着弹？", "连好了我就继续", "I can't
look that up", "你刚才暂停了"), and neither does what follows a question, which waits on its answer ("想听哪一首？我这就
弹给你～"). Wrongly spotted, a reply is only asked for once more, but that is another model call, a second or two: 9 of
the 10 replies deepseek-v4.1-flash had held back in the real-model check (2026-11-04) claimed nothing."""

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
AT_ONCE = (r"(?:这就|這就|那就|那我就|马上|馬上|立刻|立马|现在就|現在就|我来|我來|我去|我先|我再|让我|讓我|帮你|幫你|替你|给你"
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
    r"\bI'?ll (?:play|replay|pause|stop|resume|continue|carry on|pick up|restart|start|retry|try (?:it |that |this )?"
    r"(?:again|once more)|cancel|rename|make(?! sure)|create|save|turn|set|skip|shuffle|put|go on)\b",  # not "try to"
    r"\b(?:I'm|I am) (?:now |already )?(?:playing|replaying|pausing|stopping|resuming|continuing|restarting|starting"
    r"|retrying|cancell?ing|renaming|making|creating|saving|turning|setting|skipping|shuffling|putting)\b",
    r"\bI(?:'ve| have) (?:just |now )?(?:replayed|paused|stopped|resumed|continued|restarted|started|retried"
    r"|cancell?ed|renamed|made|created|saved|turned|set|skipped|shuffled|put)\b",  # not "I've played", as before
    r"\blet me (?:play|replay|pause|stop|resume|continue|carry on|restart|start|retry|try (?:it |that |this )?"
    r"(?:again|once more)|cancel|rename|make(?! sure)|save|turn|set|skip|shuffle)\b",
    r"\blet's (?:carry on|continue|start|begin|play|go on|pick up|listen)\b",
    # Not what the sentence is about ("Playing piano before bed is a lovely habit").
    r"^(?:okay|ok|sure|alright|all right|of course|got it|great)?[\s!.—–-]*(?:(?:playing|replaying|pausing|stopping"
    r"|resuming|continuing|restarting|starting|retrying|cancell?ing|renaming|skipping|shuffling)\b"
    r"(?![^.!?]*\b(?:is|are|was|were)\b)|(?:paused|stopped|cancell?ed|renamed|done|next one|back to)\b)",
    r"\b(?:retrying|trying again|picking up|carrying on|coming up|here we go|from the (?:top|start)|now playing"
    r"|all done)\b",
    r"\bhere's\b(?! (?:what|how|why|where|who|a |an |the |my |your |our |some|everything|all))"
    r"|\bhere's (?:the|your) (?:piece|song)\b",  # not "here's your most played", "here's the story"
    r"\bvolume (?:up|down)\b|\bturn(?:ed|ing)? (?:it )?(?:up|down)\b|\bvolume (?:is )?(?:now )?(?:set )?(?:at|to) \d",
    # Not what plays, as asked ("Clair de Lune is playing right now").
    r"\b(?:is|are) (?:now )?playing\b(?! right now)|\bplaying (?:it |this |that )?(?:now|again|for you)\b",
    r"\bplaylist (?:is )?(?:ready|made|saved|created)\b",
]), re.I)
# Of those, what says only that the piece is paused where it is ("先停在这儿", "停在22秒", "停住啦", "paused"), not
# stopped: true already when it is paused.
PAUSED = re.compile(r"先[^，。]{0,12}?(?:停|暂停|暫停)|停在|停住|暂停|暫停|\bpaus(?:e|ed|ing)\b", re.I)
# What she says she is going to look for: true only when something follows it, so it counts when her reply ends with
# it ("我去网上找一版～", "I'll find a version of it."), not before an answer ("我去看看你最近都听了些什么～ 最多的是
# 《月光》").
GOING = re.compile("|".join([
    rf"{AT_ONCE}{LOOK}",  # 我去找一版, 我先查查你的曲库
    r"\bI'?ll (?:find|look|search|check|get)\b", r"\blet me (?:find|look|search|check|get)\b",
]), re.I)

# What waits on the user (their connecting, their turning a site on, their telling her), or on anything else first,
# or comes later, says what will be, not what is: in Chinese, from the words that say so on ("等你连好了，我再继续",
# "你在手机上把哔哩哔哩也打开，我再帮你找一版", "连好了跟我说一声，我接着弹", "稍后再让我试试"); in English, the whole
# sentence ("I'll go on when you're ready").
WAITS_ON = re.compile(r"如果|要是|的话|的話|只要|一旦|你连|你連|等你|等您|等(?![我待])[^，。]{0,10}?(?:后|後|再|了|好|时|時)"
                      r"|打开|打開|告诉我|告訴我|说一声|說一聲|叫我|之后|之後|稍后|稍後|过会|過會|等会|等會|回头|回頭"
                      r"|等我们|等我們")
WAITS_ON_EN = re.compile(r"\b(?:if|when you|whenever|once (?:you|it|the|your)|as soon as|until|unless|after you"
                         r"|and I'll)\b", re.I)
# A part of a sentence that offers or asks, says what was not done, or what the user did (你 first, as its subject;
# not 你的), or what follows from something else first ("好了就弹给你", "连好了我就继续"; not "这就弹").
CLAIMS_NOTHING = re.compile(
    r"可以|能|要不要|要我|需要我|想不想|想听|想聽|想要|随时|隨時|愿意|願意|(?:[吗嗎]|(?<![什怎这這那多])么)$"
    r"|(?:了|好|后|後|完|声|聲|去|来|來)[^，。]{0,3}?就"
    r"|(?<!差)(?:不|没|沒|别|別|未|无法|無法)(?![过過错錯少久妨仅僅但然只断斷])"
    r"|^(?:不过|不過|对了|對了|那|而且|其实|其實|可是|但是?|好|嗯)?[你您](?![的们們])"
    r"|\b(?:can|could|would|shall|should|may|might|want me|happy to|feel free|any ?time|just say|say the word"
    r"|let me know|you'd like|not|never|nothing|no longer|cannot|unable)\b|n't\b", re.I)
# Where a sentence ends: after its mark (a full stop only before a space, not in "Op.9" or "3.5").
END = re.compile(r"(?<=[。！？!?；;\n～~…])|(?<=\.)(?=\s)")
CLAUSE = re.compile(r"[，,、：:—–]+")
ASKS = re.compile(r"(?:[吗嗎]|(?<![什怎这這那多])么)$")  # a question without its mark ("要我接着放吗～"), not 什么


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
    """What words say: that something is done or is being done at once, what her tools do, or, when they end with it,
    that she goes to look for something ("done"; "paused" when it is only that the piece is paused where it is). Up to
    a question only: what follows it waits on its answer. The conversation's notes of what was done, written as words,
    are "done" too."""
    said = {"done"} if MARKER.search(words) else set()
    sentences = _sentences(words)
    for sentence, asks in sentences:
        if asks:
            return said
        said.update("paused" if PAUSED.search(clause) else "done" for clause in _claiming(sentence, DONE))
    if sentences and _claiming(sentences[-1][0], GOING):
        said.add("done")
    return said

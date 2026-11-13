"""compose.py —— 文本清洗、关键词提取、兜底文案。

这里不调用任何 LLM：LLM 工具由模型自己产出 comment；
主动分享时用这里的自然口语模板兜底，避免再开一次模型调用（省内存/省 token）。
"""

from __future__ import annotations

import html
import random
import re

_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")
_AT_RE = re.compile(r"@\S+")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[，。！？、；：“”‘’（）【】…~～\?\!,\.;:\"'\(\)\[\]\{\}]+")

# 句首常见的无信息量引导词，去掉后再拿去搜
_LEAD_WORDS = (
    "我想看",
    "我想",
    "想看",
    "想听",
    "想了解",
    "推荐点",
    "推荐一下",
    "推荐些",
    "推荐",
    "给我",
    "帮我",
    "麻烦",
    "来点",
    "来一个",
    "来个",
    "有没有",
    "有什么",
    "找一下",
    "找个",
    "找点",
    "搜一下",
    "搜搜",
    "查一下",
    "看看",
    "讲一下",
    "说说",
    "关于",
)

_TRAIL_WORDS = ("吧", "呢", "啊", "呀", "吗", "哦", "嘛", "了", "的", "哈")

_COMMENT_TEMPLATES = (
    "刚刷到这个，感觉你会喜欢：{title}",
    "随手扒到一个还挺对味的：{title}",
    "这个我看了下，蛮有意思的，{author} 的《{title}》",
    "刚好想起这个，{title}，看完估计你会想聊两句",
    "看到这个就想到刚才聊的了：{title}",
)


def clean_title(raw) -> str:
    """B 站搜索结果的标题带 <em class="keyword"> 高亮标签，必须清掉。"""
    if not isinstance(raw, str):
        return "未知标题"
    text = html.unescape(_TAG_RE.sub("", raw))
    text = _SPACE_RE.sub(" ", text).strip()
    return text or "未知标题"


def format_count(raw) -> str:
    if isinstance(raw, str):
        text = raw.strip()
        return text or "未知"
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value >= 100000000:
            return f"{value / 100000000:.1f}亿"
        if value >= 10000:
            return f"{value / 10000:.1f}万"
        return str(int(value))
    return "未知"


def clean_keyword(text: str, max_len: int = 16) -> str:
    """从一句人话里抠出可以拿去搜视频的关键词。"""
    if not text:
        return ""
    text = _CQ_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _AT_RE.sub(" ", text)
    text = text.replace("\n", " ")
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if not text:
        return ""
    # 按空格切段，优先取最像主题的那一段
    segments = [s for s in text.split(" ") if s]
    if not segments:
        return ""
    segments.sort(key=len, reverse=True)
    candidate = segments[0]
    for word in _LEAD_WORDS:
        if candidate.startswith(word) and len(candidate) > len(word) + 1:
            candidate = candidate[len(word) :]
            break
    for word in _TRAIL_WORDS:
        if candidate.endswith(word) and len(candidate) > 2:
            candidate = candidate[:-1]
            break
    candidate = candidate.strip()
    if len(candidate) > max_len:
        candidate = candidate[:max_len]
    return candidate


def fallback_comment(title: str, author: str = "", bvid: str = "") -> str:
    """没给 comment 时用一句自然口语顶上（不是机器味的「为您推荐」）。"""
    title = clean_title(title)
    desc = f"UP：{author}" if author else ""
    if bvid:
        desc = f"{desc} · {bvid}" if desc else bvid
    tpl = random.choice(_COMMENT_TEMPLATES)
    line = tpl.format(title=f"《{title}》", author=author or "这位 UP")
    if desc:
        return f"{line}（{desc}）"
    return line

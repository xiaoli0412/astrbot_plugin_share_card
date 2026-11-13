"""search.py —— 复用 astrbot_plugin_bilibili 的搜索能力（不重写 WBI 签名）。

两级策略：
1. 优先拿「已加载的 astrbot_plugin_bilibili 实例」上的 bili_client —— 共享它已登录的
   凭据、代理、连接池，零额外开销。
2. 拿不到就把 bilibili 插件目录塞进 sys.path，import 它的 `bili_client.BiliClient`
   自己 new 一个（匿名可用，只是容易被打限流）。
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any

from astrbot.api import logger

_CLIENT: Any = None
_CLIENT_SRC: str = ""


def _bili_root_dir(cfg_path: str) -> str:
    candidates = []
    if cfg_path:
        candidates.append(cfg_path.strip())
    candidates += [
        "/AstrBot/data/plugins/astrbot_plugin_bilibili",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "astrbot_plugin_bilibili",
        ),
    ]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "bili_client.py")):
            return path
    return ""


def _reuse_loaded_client() -> Any:
    """从 star_map 里捞 bilibili 插件的既有实例。"""
    try:
        from astrbot.core.star.star import star_map
    except Exception:  # noqa: BLE001
        return None
    for module_path, meta in list(star_map.items()):
        root = getattr(meta, "root_dir_name", None) or ""
        name = getattr(meta, "name", None) or ""
        if "astrbot_plugin_bilibili" not in f"{module_path} {root} {name}":
            continue
        inst = getattr(meta, "star_cls", None)  # 注意：这里是实例，不是类
        client = getattr(inst, "bili_client", None)
        if client is not None:
            logger.info("[share_card] 复用 astrbot_plugin_bilibili 的 bili_client")
            return client
    return None


def get_client(cfg_path: str = "") -> tuple[Any, str]:
    """返回 (client, 来源说明)。拿不到返回 (None, 原因)。"""
    global _CLIENT, _CLIENT_SRC
    if _CLIENT is not None:
        return _CLIENT, _CLIENT_SRC

    client = _reuse_loaded_client()
    if client is not None:
        _CLIENT, _CLIENT_SRC = client, "reuse:astrbot_plugin_bilibili"
        return _CLIENT, _CLIENT_SRC

    root = _bili_root_dir(cfg_path)
    if not root:
        return None, "找不到 astrbot_plugin_bilibili 目录（bili_plugin_path 配置不对？）"
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        module = importlib.import_module("bili_client")
        client = module.BiliClient()
        _CLIENT, _CLIENT_SRC = client, f"new:{root}"
        logger.info(f"[share_card] 自建 BiliClient（来源 {root}）")
        return _CLIENT, _CLIENT_SRC
    except Exception as e:  # noqa: BLE001
        return None, f"导入 BiliClient 失败: {type(e).__name__}: {e}"


def _stat_of(item: dict) -> dict:
    stat = item.get("stat")
    return stat if isinstance(stat, dict) else {}


def normalize(item: dict, kind: str = "search") -> dict | None:
    """把三种来源（搜索/热门/视频详情）统一成同一种结构。"""
    from sharecard.compose import clean_title

    if not isinstance(item, dict):
        return None
    bvid = item.get("bvid") or ""
    if not bvid:
        return None

    if kind == "hot":
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        stat = _stat_of(item)
        return {
            "title": clean_title(item.get("title")),
            "bvid": bvid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "pic": item.get("pic") or "",
            "author": owner.get("name") or "未知UP",
            "view": stat.get("view"),
            "like": stat.get("like"),
            "danmaku": stat.get("danmaku"),
            "pubdate": item.get("pubdate"),
        }

    if kind == "info":
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        stat = _stat_of(item)
        return {
            "title": clean_title(item.get("title")),
            "bvid": bvid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "pic": item.get("pic") or "",
            "author": owner.get("name") or "未知UP",
            "view": stat.get("view"),
            "like": stat.get("like"),
            "danmaku": stat.get("danmaku"),
            "pubdate": item.get("pubdate"),
        }

    # 搜索接口：字段更平；标题带 <em> 标签；封面是 // 开头
    return {
        "title": clean_title(item.get("title")),
        "bvid": bvid,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "pic": item.get("pic") or "",
        "author": item.get("author") or item.get("uname") or "未知UP",
        "view": item.get("play"),
        "like": item.get("like"),
        "danmaku": item.get("video_review"),
        "pubdate": item.get("pubdate"),
    }


def _first(items: list, skip: set[str] | None = None) -> dict | None:
    skip = skip or set()
    for item in items:
        if isinstance(item, dict) and item.get("bvid"):
            if str(item.get("bvid")).lower() in skip:
                continue
            return item
    return None


async def search_one(
    cfg_path: str,
    keyword: str,
    order: str = "totalrank",
    skip: set[str] | None = None,
) -> tuple[dict | None, str]:
    """搜第一个视频（可跳过最近已发过的 bvid）。keyword 为空时退化为全站热门。"""
    skip = {s.lower() for s in (skip or set())}
    client, src = get_client(cfg_path)
    if client is None:
        return None, src

    keyword = (keyword or "").strip()
    try:
        if keyword:
            payload = await client.search_videos(
                keyword, order=order, page=1, page_size=8
            )
            if not payload:
                return None, "搜索接口无返回（可能被风控）"
            items = payload.get("result") or []
            raw = _first(items, skip)
            kind = "search"
        else:
            payload = await client.get_hot_videos(pn=1, ps=8)
            if not payload:
                return None, "热榜接口无返回（可能被风控）"
            items = payload.get("list") or []
            raw = _first(items, skip)
            kind = "hot"
    except Exception as e:  # noqa: BLE001
        return None, f"调用 bili 客户端异常: {type(e).__name__}: {e}"

    if raw is None:
        return None, f"没搜到视频（关键词：{keyword or '热榜'}）"
    video = normalize(raw, kind)
    if video is None:
        return None, "结果缺少 bvid"
    return video, src


async def get_by_bvid(cfg_path: str, bvid: str) -> tuple[dict | None, str]:
    client, src = get_client(cfg_path)
    if client is None:
        return None, src
    bvid = (bvid or "").strip()
    if not bvid.upper().startswith("BV"):
        return None, f"不是合法 BV 号: {bvid}"
    try:
        data = await client.get_video_info(bvid)
    except Exception as e:  # noqa: BLE001
        return None, f"取视频详情异常: {type(e).__name__}: {e}"
    if not data or not isinstance(data.get("info"), dict):
        return None, f"取不到 {bvid} 的详情"
    video = normalize(data["info"], "info")
    if video is None:
        return None, "详情里没有 bvid"
    return video, src

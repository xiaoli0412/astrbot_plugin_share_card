"""ark.py —— NapCat 小程序卡片（ark）能力封装。

实测结论（AstrBot + NapCat v4.18.37）：
- `get_mini_app_ark` 可用，type="bili" 模板存在，返回
  {"data": {"ver","prompt","config","app","view","meta","miniappShareOrigin","miniappOpenRefer"}}
- 只有 `json` 消息段能把这份 ark 真的渲染成卡片；
  `miniapp` 段在 NapCat 里是空实现，会报「消息体无法解析」。
- `send_ark_share` 虽然能调通，但生成的是「推荐好友」名片（view=contact），
  不是视频卡片，与需求无关。
"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger

MAX_TITLE = 40
MAX_DESC = 60


def fix_pic_url(pic: str) -> str:
    """B 站搜索接口返回的封面是 `//i0.hdslb.com/...`，必须补协议。"""
    pic = (pic or "").strip()
    if not pic:
        return ""
    if pic.startswith("//"):
        return "https:" + pic
    if pic.startswith("http://"):
        return "https://" + pic[len("http://") :]
    return pic


def _looks_like_ark(data: Any) -> bool:
    return isinstance(data, dict) and "ver" in data and "app" in data and "meta" in data


def unwrap_ark(resp: Any) -> dict | None:
    """从 call_action 返回里取出真正的 ark 结构，兼容被包一层 data 的情况。"""
    if _looks_like_ark(resp):
        return resp
    if isinstance(resp, dict):
        inner = resp.get("data")
        if _looks_like_ark(inner):
            return inner
    return None


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def build_bili_ark(
    bot: Any,
    *,
    title: str,
    desc: str,
    jump_url: str,
    pic_url: str,
) -> tuple[dict | None, dict]:
    """调 NapCat 的 get_mini_app_ark 生成 B 站小程序卡片结构。

    返回 (ark_inner, debug)。失败时 ark_inner 为 None，debug 里带错误。
    """
    params = {
        "type": "bili",
        "title": _clip(title, MAX_TITLE),
        "desc": _clip(desc, MAX_DESC),
        "picUrl": fix_pic_url(pic_url),
        "jumpUrl": jump_url,
    }
    debug: dict[str, Any] = {"params": params}
    try:
        resp = await bot.api.call_action("get_mini_app_ark", **params)
    except Exception as e:  # noqa: BLE001
        debug["error"] = f"{type(e).__name__}: {e}"
        return None, debug
    debug["resp"] = resp
    inner = unwrap_ark(resp)
    if inner is None:
        debug["error"] = "返回结构里没有找到 ark（ver/app/meta）"
    return inner, debug


def json_segment(ark_inner: dict) -> dict:
    """把 ark 打包成 OneBot v11 的 json 消息段（NapCat 唯一真正渲染卡片的段）。"""
    return {
        "type": "json",
        "data": {"data": json.dumps(ark_inner, ensure_ascii=False)},
    }


def text_segment(text: str) -> dict:
    return {"type": "text", "data": {"text": text}}


async def _send(bot: Any, is_group: bool, target_id: Any, segments: list) -> dict:
    if is_group:
        return await bot.api.call_action(
            "send_group_msg", group_id=int(target_id), message=segments
        )
    return await bot.api.call_action(
        "send_private_msg", user_id=int(target_id), message=segments
    )


async def send_card(
    bot: Any, *, is_group: bool, target_id: Any, ark_inner: dict
) -> dict:
    """发一张卡片。返回 {"ok": bool, ...}。"""
    try:
        ret = await _send(bot, is_group, target_id, [json_segment(ark_inner)])
        return {"ok": True, "ret": ret, "seg": "json"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[share_card] 卡片(json 段)发送失败: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "seg": "json"}


async def send_text(bot: Any, *, is_group: bool, target_id: Any, text: str) -> dict:
    try:
        ret = await _send(bot, is_group, target_id, [text_segment(text)])
        return {"ok": True, "ret": ret}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[share_card] 文本发送失败: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

"""astrbot_plugin_share_card —— 主动分享 B 站视频为 QQ 小程序卡片。

核心结论（实测 NapCat v4.18.37）：
- `get_mini_app_ark(type="bili")` 可用；
- 只有 `json` 消息段能真的渲染成卡片，`miniapp` 段是空实现；
- 卡片发出去之后紧跟 1-2 句人话，观感才对。

三个入口：
1. LLM 工具 `share_video(keyword, comment)` —— 对话中让模型自己决定推什么
2. 主动分享（默认关）—— 按间隔从最近对话里抠关键词推
3. 指令 `/卡片测试 <BV号或关键词>`、`/卡片状态`
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys
import traceback
from typing import Any

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from astrbot.api import AstrBotConfig, logger  # noqa: E402
from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402
from astrbot.core.star.filter.command import GreedyStr  # noqa: E402

from sharecard import ark as ark_mod  # noqa: E402
from sharecard import compose as compose_mod  # noqa: E402
from sharecard import search as search_mod  # noqa: E402

MAX_RECORDS = 20
MAX_RECENT = 10


def _today() -> str:
    return datetime.date.today().isoformat()


@register(
    "astrbot_plugin_share_card",
    "xiaoli0412",
    "卡片分享：用 NapCat get_mini_app_ark 发 B站 小程序卡片，配 1-2 句文案",
    "0.1.0",
    "",
)
class ShareCardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.cfg: dict = dict(config or {})
        self._task: asyncio.Task | None = None
        self._last: dict[str, Any] = {}
        self._state: dict[str, Any] = {
            "date": _today(),
            "count": 0,
            "records": [],
            "recent": [],
        }

    # ---------------- 生命周期 ----------------

    async def initialize(self) -> None:
        try:
            raw = await self.get_kv_data("state", None)
            if isinstance(raw, dict):
                self._state.update(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[share_card] 读取状态失败: {e}")
        if self._state.get("date") != _today():
            self._state["date"] = _today()
            self._state["count"] = 0
        logger.info(
            "[share_card] 已加载 | enabled=%s proactive=%s interval=%sh max/day=%s source=%s"
            % (
                self._cfg("enabled", False),
                self._cfg("proactive_enabled", False),
                self._cfg("proactive_interval_hours", 6),
                self._cfg("max_per_day", 3),
                self._cfg("search_source", "bilibili"),
            )
        )
        if self._cfg("enabled", False) and self._cfg("proactive_enabled", False):
            self._task = asyncio.create_task(self._proactive_loop())
            logger.info("[share_card] 主动分享任务已启动")

        # PATCHED(local): 注册 WebUI 分享面板 API
        try:
            reg = getattr(self.context, "register_web_api", None)
            if reg:
                reg("astrbot_plugin_share_card/overview", self.api_overview,
                    ["GET"], "分享：闸门状态 + 记录")
                reg("astrbot_plugin_share_card/test_push", self.api_test_push,
                    ["POST"], "分享：手动触发一次")
                logger.info("[share_card] WebUI API 已注册 2 条")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[share_card] WebUI API 注册失败: %s", exc)

        # 可重复的 NapCat 可行性自测：在 plugin_data/share_card/SELFTEST 里写一个 QQ 号，
        # 重启后本插件会自己发一张卡片到该 QQ 并把结果打进日志（跑完自动删除标记文件）。
        await self._maybe_selftest()

    async def _maybe_selftest(self) -> None:
        marker = "/AstrBot/data/plugin_data/share_card/SELFTEST"
        if not os.path.isfile(marker):
            return
        try:
            with open(marker, "r", encoding="utf-8") as f:
                qq = f.read().strip()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[share_card] 读 SELFTEST 失败: {e}")
            return
        try:
            os.remove(marker)
        except OSError:
            pass
        asyncio.create_task(self._selftest(qq))

    async def _selftest(self, qq: str) -> None:
        await asyncio.sleep(20)
        logger.info(f"[share_card] SELFTEST 开始 -> {qq}")
        try:
            try:
                from astrbot.core.star.star_handler import star_handlers_registry

                reg = []
                for h in star_handlers_registry:
                    if "astrbot_plugin_share_card" in (h.handler_module_path or ""):
                        et = getattr(h.event_type, "name", str(h.event_type))
                        reg.append(f"{et}:{h.handler_name}")
                logger.info(f"[share_card] SELFTEST 已注册 handler: {reg}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[share_card] SELFTEST 读 handler 注册表失败: {e}")
            video, note = await search_mod.search_one(
                self._cfg("bili_plugin_path", ""), "猫咪"
            )
            if video is None:
                logger.error(f"[share_card] SELFTEST 失败：{note}")
                return
            logger.info(
                "[share_card] SELFTEST 命中 %s / %s / pic=%s"
                % (video.get("title"), video.get("bvid"), video.get("pic"))
            )
            res = await self._push_video(
                self._get_bot(),
                is_group=False,
                target=qq,
                video=video,
                comment="这只猫看着还挺解压的，你要不要看看",
                via="selftest",
            )
            logger.info(
                "[share_card] SELFTEST 结果 %s"
                % json.dumps(res, ensure_ascii=False, default=str)[:1500]
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[share_card] SELFTEST 异常: {e}\n{traceback.format_exc()}")
        logger.info("[share_card] SELFTEST 结束")

    # ------------------------------------------------ WebUI（PATCHED local）
    async def api_overview(self):
        """分享记录 + 当前闸门状态。"""
        import datetime as _dt
        now = _dt.datetime.now().timestamp()
        last = self._last or {}
        last_talk = float(last.get("ts") or 0)
        idle_h = (now - last_talk) / 3600.0 if last_talk else -1
        last_share = float(self._state.get("last_share_ts") or 0)
        gap_h = (now - last_share) / 3600.0 if last_share else -1
        return {
            "ok": True,
            "data": {
                "enabled": bool(self._cfg("enabled", False)),
                "proactive_enabled": bool(self._cfg("proactive_enabled", False)),
                "idle_trigger_hours": float(self._cfg("idle_trigger_hours", 6)),
                "idle_max_hours": float(self._cfg("idle_max_hours", 72)),
                "min_share_gap_hours": float(self._cfg("min_share_gap_hours", 8)),
                "check_interval_minutes": int(self._cfg("check_interval_minutes", 30)),
                "today_count": int(self._state.get("count", 0)),
                "max_per_day": int(self._cfg("max_per_day", 3)),
                "idle_hours": round(idle_h, 2),
                "share_gap_hours": round(gap_h, 2),
                "last_target": last.get("target"),
                "records": list(reversed(self._state.get("records") or [])),
            },
        }

    async def api_test_push(self):
        """手动触发一次分享（调试用）。"""
        try:
            await self._proactive_once()
            return {"ok": True, "data": {"done": True}}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def terminate(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        logger.info("[share_card] 已卸载")

    # ---------------- 配置 / 状态 ----------------

    def _cfg(self, key: str, default: Any) -> Any:
        value = self.cfg.get(key, default)
        return default if value is None else value

    async def _persist(self) -> None:
        try:
            await self.put_kv_data("state", self._state)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[share_card] 保存状态失败: {e}")

    def _bump_count(self) -> None:
        if self._state.get("date") != _today():
            self._state["date"] = _today()
            self._state["count"] = 0
        self._state["count"] = int(self._state.get("count", 0)) + 1

    def _add_record(self, **kw: Any) -> None:
        rec = {"ts": datetime.datetime.now().strftime("%m-%d %H:%M:%S"), **kw}
        records = self._state.setdefault("records", [])
        records.append(rec)
        self._state["records"] = records[-MAX_RECORDS:]

    def _remember_bvid(self, bvid: str) -> None:
        if not bvid:
            return
        recent = self._state.setdefault("recent", [])
        recent.append(bvid)
        self._state["recent"] = recent[-MAX_RECENT:]

    # ---------------- bot / 会话目标 ----------------

    def _get_bot(self) -> Any:
        try:
            for inst in self.context.platform_manager.platform_insts:
                bot = getattr(inst, "bot", None)
                if bot is not None:
                    return bot
        except Exception:  # noqa: BLE001
            pass
        return None

    def _bot_of(self, event: AstrMessageEvent) -> Any:
        bot = getattr(event, "bot", None)
        return bot if bot is not None else self._get_bot()

    @staticmethod
    def _target_of(event: AstrMessageEvent) -> tuple[bool, Any]:
        group_id = ""
        try:
            group_id = event.get_group_id() or ""
        except Exception:  # noqa: BLE001
            group_id = ""
        if group_id:
            return True, group_id
        return False, event.get_sender_id()

    # ---------------- 发送核心 ----------------

    async def _push_video(
        self,
        bot: Any,
        *,
        is_group: bool,
        target: Any,
        video: dict,
        comment: str = "",
        via: str = "tool",
    ) -> dict:
        """发卡片 + 配文案 + 失败回退纯文本。返回结果字典。"""
        if bot is None:
            return {"ok": False, "error": "拿不到 OneBot bot 实例（非 aiocqhttp 平台？）"}

        view = compose_mod.format_count(video.get("view"))
        desc = f"{video.get('author') or '未知UP'} · {view}播放"
        ark_inner, debug = await ark_mod.build_bili_ark(
            bot,
            title=video.get("title") or "B 站视频",
            desc=desc,
            jump_url=video.get("url") or "",
            pic_url=video.get("pic") or "",
        )
        if self._cfg("log_ark_debug", True):
            logger.info(
                "[share_card] ark debug | %s"
                % json.dumps(debug, ensure_ascii=False, default=str)[:1200]
            )
        if ark_inner and self._cfg("log_ark_debug", True):
            logger.info(
                "[share_card] ark keys=%s view=%s app=%s"
                % (
                    sorted(ark_inner.keys()),
                    ark_inner.get("view"),
                    ark_inner.get("app"),
                )
            )

        result: dict[str, Any] = {
            "ark_ok": bool(ark_inner),
            "ark_view": (ark_inner or {}).get("view"),
            "send": {},
            "fallback_used": False,
        }

        if ark_inner:
            send = await ark_mod.send_card(
                bot, is_group=is_group, target_id=target, ark_inner=ark_inner
            )
        else:
            send = {"ok": False, "error": debug.get("error") or "ark 生成失败"}

        result["send"] = send

        if not send.get("ok"):
            if self._cfg("fallback_to_text", True):
                text = f"{video.get('title')}\n{video.get('url')}"
                fb = await ark_mod.send_text(
                    bot, is_group=is_group, target_id=target, text=text
                )
                result["fallback_used"] = True
                result["fallback"] = fb
                result["ok"] = bool(fb.get("ok"))
            else:
                result["ok"] = False
            self._add_record(
                via=via,
                bvid=video.get("bvid"),
                title=video.get("title"),
                ok=result["ok"],
                fallback=result["fallback_used"],
                error=str(send.get("error"))[:120],
            )
            await self._persist()
            return result

        result["ok"] = True
        if comment:
            cm = await ark_mod.send_text(
                bot, is_group=is_group, target_id=target, text=comment
            )
            result["comment"] = cm
        self._remember_bvid(video.get("bvid") or "")
        self._add_record(
            via=via,
            bvid=video.get("bvid"),
            title=video.get("title"),
            ok=True,
            fallback=False,
        )
        await self._persist()
        return result

    # ---------------- LLM 工具 ----------------

    @filter.llm_tool(name="share_video")
    async def share_video(
        self,
        event: AstrMessageEvent,
        keyword: str,
        comment: str = "",
    ):
        """在当前对话里推一个哔哩哔哩视频（发成 QQ 小程序卡片，不是纯链接）。当用户想看点视频、想看某个话题的视频、或者聊到某个内容适合配个视频佐证时调用。卡片会真的发到当前会话里。

        Args:
            keyword(string): 必填。B 站搜索关键词，2-12 个字，只写核心名词，不要整句话。例如「猫」「黑神话」「量子物理入门」。
            comment(string): 可选。紧跟着卡片发出去的 1-2 句话，用你自己的语气自然说出来，不要写「为您推荐」「以下视频」这类机器话。
        """
        if not self._cfg("enabled", False):
            return "分享卡片插件当前未启用（enabled=false），无法发送。"

        keyword = compose_mod.clean_keyword(keyword or "")
        video, note = await search_mod.search_one(
            self._cfg("bili_plugin_path", ""),
            keyword,
            skip=set(self._state.get("recent") or []),
        )
        if video is None:
            return f"没找到可分享的视频：{note}"

        bot = self._bot_of(event)
        is_group, target = self._target_of(event)
        if comment:
            comment = comment.strip()
        else:
            comment = compose_mod.fallback_comment(
                video.get("title", ""), video.get("author", ""), video.get("bvid", "")
            )

        res = await self._push_video(
            bot,
            is_group=is_group,
            target=target,
            video=video,
            comment=comment,
            via="llm_tool",
        )
        if not res.get("ok"):
            return (
                f"视频找到了（{video.get('title')} / {video.get('bvid')}），"
                f"但发送失败：{res.get('send', {}).get('error') or res.get('error')}"
            )
        tail = "（卡片失败，已回退纯文本链接）" if res.get("fallback_used") else ""
        return (
            f"已发送卡片：{video.get('title')} / {video.get('bvid')} "
            f"（关键词：{keyword or '热榜'}）{tail}。"
            "卡片后已经带上了你说的那句话，不用再重复描述视频。"
        )

    # ---------------- 指令 ----------------

    @filter.command("卡片测试")
    async def cmd_card_test(self, event: AstrMessageEvent, arg: GreedyStr):
        """实测发一张卡片：/卡片测试 <BV号 或 关键词>"""
        raw = str(arg or "").strip()
        cfg_path = self._cfg("bili_plugin_path", "")

        note = ""
        video = None
        if raw:
            bvid = _extract_bvid(raw)
            if bvid:
                video, note = await search_mod.get_by_bvid(cfg_path, bvid)
            else:
                video, note = await search_mod.search_one(cfg_path, raw)
        else:
            video, note = await search_mod.search_one(cfg_path, "")

        if video is None:
            yield event.plain_result(f"❌ 取视频失败：{note}")
            return

        bot = self._bot_of(event)
        is_group, target = self._target_of(event)
        comment = compose_mod.fallback_comment(
            video.get("title", ""), video.get("author", ""), video.get("bvid", "")
        )
        res = await self._push_video(
            bot,
            is_group=is_group,
            target=target,
            video=video,
            comment=comment,
            via="cmd_test",
        )
        self._bump_count()
        await self._persist()

        lines = [
            "🧪 卡片测试结果",
            f"命中：{video.get('title')}",
            f"BV：{video.get('bvid')}  UP：{video.get('author')}  播放：{compose_mod.format_count(video.get('view'))}",
            f"封面：{ark_mod.fix_pic_url(video.get('pic') or '')}",
            f"ark 生成：{'✅ 成功' if res.get('ark_ok') else '❌ 失败'}  view={res.get('ark_view')}",
            f"卡片发送：{'✅ 成功' if res.get('send', {}).get('ok') else '❌ 失败'}  {res.get('send', {}).get('ret') or res.get('send', {}).get('error')}",
            f"回退纯文本：{'已触发' if res.get('fallback_used') else '未触发'}",
            f"配文：{comment}",
            f"bot：{type(bot).__name__ if bot is not None else 'None'}",
            f"目标：{'群 ' if is_group else '私聊 '}{target}",
        ]
        if note:
            lines.append(f"搜索来源：{note}")
        yield event.plain_result("\n".join(lines))

    @filter.command("卡片状态")
    async def cmd_card_status(self, event: AstrMessageEvent):
        """查看本插件配置与最近发送记录"""
        _, src = search_mod.get_client(self._cfg("bili_plugin_path", ""))
        today = self._state.get("date")
        count = self._state.get("count") if today == _today() else 0
        lines = [
            "📇 share_card 状态",
            f"enabled：{self._cfg('enabled', False)}",
            f"proactive_enabled：{self._cfg('proactive_enabled', False)}",
            f"proactive_interval_hours：{self._cfg('proactive_interval_hours', 6)}",
            f"max_per_day：{self._cfg('max_per_day', 3)}（今日已主动推送 {count}）",
            f"search_source：{self._cfg('search_source', 'bilibili')}",
            f"bili_plugin_path：{self._cfg('bili_plugin_path', '')}",
            f"fallback_to_text：{self._cfg('fallback_to_text', True)}",
            f"log_ark_debug：{self._cfg('log_ark_debug', True)}",
            f"搜索客户端：{src}",
            f"最近发送：{len(self._state.get('records') or [])} 条",
        ]
        for rec in (self._state.get("records") or [])[-5:]:
            flag = "✅" if rec.get("ok") else "❌"
            fb = " (回退)" if rec.get("fallback") else ""
            lines.append(
                f"  {flag} {rec.get('ts')} [{rec.get('via')}] {rec.get('bvid')} "
                f"{str(rec.get('title'))[:24]}{fb}"
            )
        yield event.plain_result("\n".join(lines))

    # ---------------- 被动记录（给主动分享取话题） ----------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        try:
            text = (event.message_str or "").strip()
            is_group, target = self._target_of(event)
            self._last = {
                "umo": event.unified_msg_origin,
                "is_group": is_group,
                "target": target,
                "text": text,
                "ts": datetime.datetime.now().timestamp(),
            }
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 主动分享 ----------------

    async def _proactive_loop(self) -> None:
        """PATCHED(local): 定时轮询 → 空闲检查。

        不再「每 N 小时必发一条」，改成「每 N 分钟检查一次条件，条件成立才发」。
        条件是：双方已经 idle_trigger_hours 没互动了，且距上次分享也够久。
        """
        await asyncio.sleep(90)  # 让平台先连上
        while True:
            check_min = max(5, int(self._cfg("check_interval_minutes", 30)))
            try:
                await self._proactive_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"[share_card] 主动分享异常: {e}\n{traceback.format_exc()}"
                )
            await asyncio.sleep(check_min * 60)

    async def _proactive_once(self) -> None:
        if not (self._cfg("enabled", False) and self._cfg("proactive_enabled", False)):
            return
        if not self._last:
            logger.info("[share_card] 主动分享跳过：还没见过任何会话消息")
            return

        # ── PATCHED(local): 空闲触发闸门 ──────────────────────────────
        # 原逻辑是「12 小时内没对话就跳过」，正好反了：人在的时候才发，
        # 冷场的时候反而不发。现在反过来——冷场了才发。
        now_ts = datetime.datetime.now().timestamp()
        last_talk = float(self._last.get("ts") or 0)
        idle_hours = (now_ts - last_talk) / 3600.0

        idle_trigger = float(self._cfg("idle_trigger_hours", 6))
        idle_max = float(self._cfg("idle_max_hours", 72))
        share_gap = float(self._cfg("min_share_gap_hours", 8))

        if idle_hours < idle_trigger:
            logger.info(
                "[share_card] 主动分享跳过：刚聊过（%.1fh < %.1fh），不打扰",
                idle_hours, idle_trigger,
            )
            return
        if idle_hours > idle_max:
            logger.info(
                "[share_card] 主动分享跳过：太久没互动（%.0fh > %.0fh），不硬推",
                idle_hours, idle_max,
            )
            return

        last_share = float(self._state.get("last_share_ts") or 0)
        if last_share and (now_ts - last_share) / 3600.0 < share_gap:
            logger.info(
                "[share_card] 主动分享跳过：距上次分享仅 %.1fh（需 %.0fh）",
                (now_ts - last_share) / 3600.0, share_gap,
            )
            return
        if self._state.get("date") != _today():
            self._state["date"] = _today()
            self._state["count"] = 0
        if int(self._state.get("count", 0)) >= int(self._cfg("max_per_day", 3)):
            logger.info("[share_card] 主动分享跳过：已达今日上限")
            return

        keyword = compose_mod.clean_keyword(self._last.get("text") or "")
        video, note = await search_mod.search_one(
            self._cfg("bili_plugin_path", ""),
            keyword,
            skip=set(self._state.get("recent") or []),
        )
        if video is None:
            logger.info(f"[share_card] 主动分享跳过：{note}")
            return

        bot = self._get_bot()
        if bot is None:
            logger.warning("[share_card] 主动分享跳过：拿不到 bot")
            return

        comment = compose_mod.fallback_comment(
            video.get("title", ""), video.get("author", ""), video.get("bvid", "")
        )
        res = await self._push_video(
            bot,
            is_group=bool(self._last.get("is_group")),
            target=self._last.get("target"),
            video=video,
            comment=comment,
            via="proactive",
        )
        if res.get("ok"):
            self._bump_count()
            # PATCHED(local): 记录本次分享时间，供 min_share_gap_hours 判断
            self._state["last_share_ts"] = datetime.datetime.now().timestamp()
            await self._persist()
        logger.info(
            f"[share_card] 主动分享完成 | 关键词={keyword or '热榜'} | "
            f"bvid={video.get('bvid')} | ok={res.get('ok')} fallback={res.get('fallback_used')}"
        )


def _extract_bvid(text: str) -> str:
    import re

    match = re.search(r"(BV[0-9A-Za-z]{10})", text or "")
    if match:
        return match.group(1)
    match = re.search(r"av(\d+)", text or "", re.I)
    if match:
        return ""  # av 号交给关键词路径处理，避免多写一个转换接口
    return ""

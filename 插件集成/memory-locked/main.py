# -*- coding: utf-8 -*-
import asyncio
import json
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools

from .memory_system import MemorySystem

ARCHIVE_INTERVAL = 24 * 3600          # 每日归档间隔
CONTEXT_BUDGET = 1500                  # 注入上下文的字符预算


class BrainMemoryPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        data_dir = StarTools.get_data_dir()      # 插件独立数据目录
        self.ms = MemorySystem(root=str(data_dir / "memory"))
        self._archive_task = asyncio.create_task(self._daily_archive_loop())
        logger.info("长期记忆插件已加载，数据目录: %s", data_dir)

    # ---------- 自动回忆：LLM 请求前注入记忆 ----------
    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req):
        try:
            query = event.message_str or ""
            ctx_text = self.ms.build_context(query, max_chars=CONTEXT_BUDGET)
            if ctx_text:
                req.system_prompt = (
                    (req.system_prompt or "")
                    + "\n\n# 关于用户的长期记忆（自动检索，仅供参考）\n"
                    + ctx_text
                )
        except Exception as e:
            logger.error("记忆注入失败: %s", e)

    # ---------- 聊天指令 ----------
    @filter.command("记住")
    async def cmd_remember(self, event: AstrMessageEvent):
        """保存一条记忆。用法：/记住 内容"""
        content = event.message_str.replace("记住", "", 1).strip()
        if not content:
            yield event.plain_result("用法：/记住 要保存的内容")
            return
        r = self.ms.remember(
            title=content[:20], content=content,
            importance=0.7, source=f"user:{event.get_sender_name()}")
        msg = "已经记住了（原有记忆得到强化）" if r["action"] == "reinforced" \
            else f"已记住 ✅（S{r['tier']} 层）"
        yield event.plain_result(msg)

    @filter.command("永久记住")
    async def cmd_pin(self, event: AstrMessageEvent):
        """永久记住，永不遗忘。用法：/永久记住 内容"""
        content = event.message_str.replace("永久记住", "", 1).strip()
        if not content:
            yield event.plain_result("用法：/永久记住 要保存的内容")
            return
        self.ms.remember(title=content[:20], content=content,
                         importance=1.0, pinned=True,
                         source=f"user:{event.get_sender_name()}")
        yield event.plain_result("已永久记住 📌，这条记忆不会衰减也不会被自动删除。")

    @filter.command("忘掉")
    async def cmd_forget(self, event: AstrMessageEvent):
        """遗忘一条记忆（30天内可恢复）。用法：/忘掉 记忆id"""
        mid = event.message_str.replace("忘掉", "", 1).strip()
        if self.ms.forget(mid):
            yield event.plain_result("已遗忘 🗑（30 天内可通过恢复功能找回）")
        else:
            yield event.plain_result("没有找到这条记忆，请用 /记忆列表 查看 id。")

    @filter.command("回忆")
    async def cmd_recall(self, event: AstrMessageEvent):
        """手动检索记忆。用法：/回忆 关键词或问题"""
        query = event.message_str.replace("回忆", "", 1).strip()
        hits = self.ms.recall(query, top_k=5)
        if not hits:
            yield event.plain_result("没有想起相关记忆。")
            return
        lines = [f"[S{h['tier']}] {h['title']}" for h in hits]
        yield event.plain_result("想起了这些：\n" + "\n".join(lines))

    @filter.command("记忆列表")
    async def cmd_list(self, event: AstrMessageEvent):
        """查看所有活跃记忆"""
        mems = self.ms.list_memories()
        if not mems:
            yield event.plain_result("还没有任何记忆，用 /记住 开始吧。")
            return
        lines = []
        for m in mems:
            pin = "📌" if m["pinned"] else "  "
            lines.append(f"{pin} S{m['tier']} [{m['importance']:.1f}] "
                         f"{m['title']}（回忆{m['recalls']}次）{m['id'][:20]}")
        yield event.plain_result("\n".join(lines))

    @filter.command("记忆统计")
    async def cmd_stats(self, event: AstrMessageEvent):
        """查看记忆分层统计"""
        yield event.plain_result(json.dumps(self.ms.stats(), ensure_ascii=False))

    # ---------- LLM 自主工具（模型在需要时自己调用） ----------
    @filter.llm_tool(name="remember_memory")
    async def tool_remember(self, event: AstrMessageEvent,
                            content: str, importance: float = 0.6):
        """当用户透露了稳定、长期有用的信息（偏好、身份、项目、决定）时，调用此工具保存记忆。

        Args:
            content(string): 要保存的完整事实内容
            importance(number): 重要度，0 到 1
        """
        r = self.ms.remember(title=content[:20], content=content,
                             importance=importance, source="llm")
        return f"memory saved: {r['action']}, id={r['id']}"

    @filter.llm_tool(name="forget_memory")
    async def tool_forget(self, event: AstrMessageEvent, keyword: str):
        """当用户明确要求忘记某件事时，调用此工具删除相关记忆。

        Args:
            keyword(string): 要遗忘内容的关键词
        """
        hits = self.ms.recall(keyword, top_k=1)
        if not hits:
            return "no related memory found"
        self.ms.forget(hits[0]["id"])
        return f"forgotten: {hits[0]['title']}"

    # ---------- 每日自动归档 ----------
    async def _daily_archive_loop(self):
        while True:
            await asyncio.sleep(ARCHIVE_INTERVAL)
            try:
                stats = self.ms.daily_archive()
                logger.info("记忆归档完成: %s", stats)
            except Exception as e:
                logger.error("记忆归档失败: %s", e)

    async def terminate(self):
        self._archive_task.cancel()

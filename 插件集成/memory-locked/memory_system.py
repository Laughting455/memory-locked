# -*- coding: utf-8 -*-
import sqlite3, os, re, time, math, json, shutil, hashlib, datetime, argparse

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
TIER_TAU = {1: 7, 2: 30, 3: 90, 4: 180}      # 各层衰减周期（天），层越高越抗衰减
TRASH_RETENTION_DAYS = 30                     # 回收站保留天数
DEMOTE_THRESHOLD = 0.35                       # 强度低于此值降层
FORGET_THRESHOLD = 0.15                       # S4 以下强度低于此值进回收站

_CJK = re.compile(r'[一-鿿]')
_ASCII_WORD = re.compile(r'[a-zA-Z0-9_]+')


def tokenize(text):
    """零依赖中文分词：ASCII 单词 + 中文二元组（bigram），供 FTS5 使用。"""
    tokens = [w.lower() for w in _ASCII_WORD.findall(text)]
    cjk_runs = re.findall(r'[一-鿿]+', text)
    for run in cjk_runs:
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i+2] for i in range(len(run) - 1))
    return tokens


class MemorySystem:
    def __init__(self, root=ROOT):
        self.root = root
        os.makedirs(f"{root}/raw", exist_ok=True)
        os.makedirs(f"{root}/trash", exist_ok=True)
        self.db = sqlite3.connect(f"{root}/index.db")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS memories(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_recalled REAL NOT NULL,
            recall_count INTEGER DEFAULT 0,
            usefulness INTEGER DEFAULT 0,
            importance REAL DEFAULT 0.5,
            tier INTEGER DEFAULT 2,
            pinned INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',      -- active / trashed / superseded
            supersedes TEXT,                    -- 本记忆取代的旧记忆 id
            content_hash TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
            id UNINDEXED, title, content, tags);
        CREATE TABLE IF NOT EXISTS rehearsal_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT, ts REAL, query TEXT);
        CREATE INDEX IF NOT EXISTS idx_status ON memories(status, tier);
        """)
        self.db.commit()

    # ================= 写入 =================
    def remember(self, title, content, tags=None, importance=0.5,
                 pinned=False, source="manual", supersedes=None):
        """写入一条记忆（原文无损）。相同内容自动去重并转为强化。"""
        tags = tags or []
        importance = max(0.0, min(1.0, float(importance)))
        chash = hashlib.sha256(content.strip().encode()).hexdigest()
        dup = self.db.execute(
            "SELECT id, tier, status FROM memories WHERE content_hash=? AND status!='trashed'",
            (chash,)).fetchone()
        if dup:  # 重复内容 → 强化已有（或取代它的）记忆，不再重复写入
            did, dtier, dstatus = dup
            if dstatus == "superseded":
                newer = self.db.execute(
                    "SELECT id, tier FROM memories WHERE supersedes=? AND status='active'",
                    (did,)).fetchone()
                if newer:
                    self._reinforce(newer[0], query="(duplicate write)")
                    self.db.commit()
                    return {"id": newer[0], "action": "reinforced", "tier": newer[1]}
            else:
                self._reinforce(did, query="(duplicate write)")
                self.db.commit()
                return {"id": did, "action": "reinforced", "tier": dtier}

        mid = f"mem_{int(time.time()*1000)}_{chash[:10]}"
        day = datetime.date.today().isoformat()
        d = f"{self.root}/raw/{day}"
        os.makedirs(d, exist_ok=True)
        path = f"{d}/{mid}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"---\ntitle: {title}\ncreated: {day}\nsource: {source}\n---\n\n")
            f.write(content)

        now = time.time()
        tier = 1 if pinned else 2
        self.db.execute(
            "INSERT INTO memories VALUES(?,?,?,?,?,0,0,?,?,?,'active',?,?)",
            (mid, title, path, now, now, importance, tier, int(pinned),
             supersedes, chash))
        self.db.execute("INSERT INTO fts VALUES(?,?,?,?)", (
            mid, " ".join(tokenize(title)),
            " ".join(tokenize(content)),
            " ".join(tokenize(" ".join(tags)))))
        if supersedes:  # 旧版本标记为被取代
            self.db.execute(
                "UPDATE memories SET status='superseded' WHERE id=?", (supersedes,))
        self.db.commit()
        return {"id": mid, "action": "created", "tier": tier}

    # ================= 回忆 =================
    def recall(self, query, top_k=5, explain=False):
        """检索记忆：命中后读取原文、记录复习、最多提升一层。"""
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        fts_q = " OR ".join(f'"{t}"' for t in q_tokens[:20])
        rows = self.db.execute(f"""
            SELECT m.id, m.title, m.path, m.tier, m.pinned, bm25(fts) AS rank
            FROM fts JOIN memories m ON fts.id = m.id
            WHERE fts MATCH ? AND m.status='active'
            ORDER BY rank * (1.0 + 0.4*m.pinned + 0.1*(5-m.tier))
            LIMIT ?""", (fts_q, top_k)).fetchall()
        results = []
        for mid, title, path, tier, pinned, rank in rows:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self._reinforce(mid, query=query)
            item = {"id": mid, "title": title, "tier": max(1, tier - 1),
                    "content": content}
            if explain:
                item["reason"] = f"BM25={rank:.2f}, 原层级 S{tier}, pinned={bool(pinned)}"
            results.append(item)
        self.db.commit()
        return results

    def _reinforce(self, mid, query):
        """复习强化：更新时间、次数、日志；最多提升一层。"""
        row = self.db.execute(
            "SELECT tier, pinned FROM memories WHERE id=?", (mid,)).fetchone()
        if not row:
            return
        tier, pinned = row
        new_tier = 1 if pinned else max(1, tier - 1)
        self.db.execute("""UPDATE memories
            SET last_recalled=?, recall_count=recall_count+1, tier=? WHERE id=?""",
            (time.time(), new_tier, mid))
        self.db.execute("INSERT INTO rehearsal_log VALUES(NULL,?,?,?)",
                        (mid, time.time(), query[:200]))

    # ================= 反馈 =================
    def feedback(self, mid, useful):
        """用户反馈记忆是否有用，微调重要度。"""
        delta = 0.1 if useful else -0.1
        self.db.execute("""UPDATE memories SET
            importance = MIN(1.0, MAX(0.0, importance + ?)),
            usefulness = usefulness + ? WHERE id=?""",
            (delta, 1 if useful else 0, mid))
        self.db.commit()

    # ================= 每日归档 =================
    def daily_archive(self):
        """衰减、降层、软删除、清理过期回收站。返回统计。"""
        now = time.time()
        stats = {"demoted": [], "trashed": [], "purged": [], "kept": 0}
        for mid, tier, pinned, imp, last, rc in self.db.execute(
                "SELECT id,tier,pinned,importance,last_recalled,recall_count "
                "FROM memories WHERE status='active'").fetchall():
            if pinned:
                stats["kept"] += 1
                continue
            strength = self._strength(imp, last, rc, tier, now)
            if tier >= 4 and strength < FORGET_THRESHOLD:
                self._soft_delete(mid)
                stats["trashed"].append(mid)
            elif strength < DEMOTE_THRESHOLD:
                self.db.execute("UPDATE memories SET tier=? WHERE id=?",
                                (min(4, tier + 1), mid))
                stats["demoted"].append(mid)
            else:
                stats["kept"] += 1
        # 清理超过保留期的回收站记忆
        cutoff = now - TRASH_RETENTION_DAYS * 86400
        for mid, path in self.db.execute(
                "SELECT id, path FROM memories WHERE status='trashed'").fetchall():
            trashed_time = os.path.getmtime(path) if os.path.exists(path) else 0
            if trashed_time < cutoff:
                self._purge(mid, path)
                stats["purged"].append(mid)
        self.db.commit()
        return stats

    @staticmethod
    def _strength(imp, last, recall_count, tier, now):
        idle_days = (now - last) / 86400
        tau = TIER_TAU.get(tier, 180)
        recency = math.exp(-idle_days / tau)
        frequency = min(1.0, math.log1p(recall_count) / math.log1p(20))
        return 0.40 * imp + 0.35 * recency + 0.25 * frequency

    # ================= 遗忘与恢复 =================
    def forget(self, mid, hard=False):
        """主动遗忘。hard=True 直接物理删除。"""
        row = self.db.execute(
            "SELECT path, status FROM memories WHERE id=?", (mid,)).fetchone()
        if not row:
            return False
        path, status = row
        if hard:
            self._purge(mid, path)
        else:
            self._soft_delete(mid)
        self.db.commit()
        return True

    def restore(self, mid):
        """从回收站恢复记忆。"""
        row = self.db.execute(
            "SELECT path FROM memories WHERE id=? AND status='trashed'", (mid,)).fetchone()
        if not row:
            return False
        trash_path = f"{self.root}/trash/{os.path.basename(row[0])}"
        if os.path.exists(trash_path):
            shutil.move(trash_path, row[0])
        self.db.execute("UPDATE memories SET status='active', last_recalled=? WHERE id=?",
                        (time.time(), mid))
        self.db.commit()
        return True

    def _soft_delete(self, mid):
        path = self.db.execute("SELECT path FROM memories WHERE id=?", (mid,)).fetchone()[0]
        if os.path.exists(path):
            shutil.move(path, f"{self.root}/trash/{os.path.basename(path)}")
        self.db.execute("UPDATE memories SET status='trashed' WHERE id=?", (mid,))

    def _purge(self, mid, path):
        for p in (path, f"{self.root}/trash/{os.path.basename(path)}"):
            if os.path.exists(p):
                os.remove(p)
        self.db.execute("DELETE FROM memories WHERE id=?", (mid,))
        self.db.execute("DELETE FROM fts WHERE id=?", (mid,))

    # ================= 上下文注入 =================
    def build_context(self, query, max_chars=2000):
        """拼接本轮应注入 LLM 的记忆：核心记忆优先，检索命中其次，受预算限制。"""
        parts, used, seen = [], 0, set()

        def add(header, path):
            nonlocal used
            if used >= max_chars:
                return
            with open(path, encoding="utf-8") as f:
                body = f.read()
            chunk = f"{header}\n{body}"
            if used + len(chunk) > max_chars:
                chunk = chunk[:max_chars - used]
            parts.append(chunk)
            used += len(chunk)

        # 1. 钉住 / 核心记忆
        for mid, path, title in self.db.execute(
                "SELECT id, path, title FROM memories "
                "WHERE status='active' AND (pinned=1 OR tier<=1) ORDER BY pinned DESC"):
            seen.add(mid)
            add(f"[核心记忆:{title}]", path)
        # 2. 本轮检索命中
        for hit in self.recall(query):
            if hit["id"] in seen:
                continue
            seen.add(hit["id"])
            add(f"[回忆:{hit['title']} · S{hit['tier']}]", self._path_of(hit["id"]))
        return "\n\n".join(parts)

    def _path_of(self, mid):
        return self.db.execute("SELECT path FROM memories WHERE id=?",
                               (mid,)).fetchone()[0]

    # ================= 解释与审计 =================
    def explain(self, mid):
        """回答“你为什么记得这个”：返回完整元数据与复习历史。"""
        row = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        if not row:
            return None
        cols = ["id","title","path","created_at","last_recalled_at","recall_count",
                "usefulness","importance","tier","pinned","status","supersedes","content_hash"]
        meta = dict(zip(cols, row))
        meta["created"] = datetime.datetime.fromtimestamp(meta.pop("created_at")).isoformat()
        meta["last_recalled"] = datetime.datetime.fromtimestamp(
            meta.pop("last_recalled_at")).isoformat()
        meta["rehearsals"] = [
            {"time": datetime.datetime.fromtimestamp(ts).isoformat(), "query": q}
            for ts, q in self.db.execute(
                "SELECT ts, query FROM rehearsal_log WHERE memory_id=? "
                "ORDER BY ts DESC LIMIT 10", (mid,)).fetchall()]
        return meta

    def list_memories(self, status="active"):
        rows = self.db.execute(
            "SELECT id,title,tier,pinned,importance,recall_count,last_recalled "
            "FROM memories WHERE status=? ORDER BY tier, pinned DESC", (status,)).fetchall()
        return [{"id": r[0], "title": r[1], "tier": r[2], "pinned": bool(r[3]),
                 "importance": r[4], "recalls": r[5],
                 "last_recalled": datetime.datetime.fromtimestamp(r[6]).strftime("%Y-%m-%d")}
                for r in rows]

    def stats(self):
        out = {"by_tier": {}, "trashed": 0, "superseded": 0, "rehearsals_total": 0}
        for tier, n in self.db.execute(
                "SELECT tier, COUNT(*) FROM memories WHERE status='active' GROUP BY tier"):
            out["by_tier"][f"S{tier}"] = n
        out["trashed"] = self.db.execute(
            "SELECT COUNT(*) FROM memories WHERE status='trashed'").fetchone()[0]
        out["superseded"] = self.db.execute(
            "SELECT COUNT(*) FROM memories WHERE status='superseded'").fetchone()[0]
        out["rehearsals_total"] = self.db.execute(
            "SELECT COUNT(*) FROM rehearsal_log").fetchone()[0]
        return out

    # ================= LLM 记忆提取提示词 =================
    @staticmethod
    def extract_prompt(conversation):
        """生成供 LLM 提取记忆的结构化提示词。"""
        return f"""请从以下对话中提取值得长期记住的事实。只提取稳定、跨会话有用的信息
（用户偏好、身份信息、项目约束、明确决定），忽略寒暄和一次性内容。
以 JSON 数组输出，每项格式：
{{"title": "简短标题", "content": "完整无损的事实原文", "tags": ["..."],
  "importance": 0.0到1.0, "pinned": true或false}}
若没有值得记住的内容，输出 []。对话如下：

{conversation}"""


# ================= 命令行入口 =================
def main():
    p = argparse.ArgumentParser(description="AI 记忆系统 v2")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("remember"); r.add_argument("title"); r.add_argument("content")
    r.add_argument("--tags", default=""); r.add_argument("--importance", type=float, default=0.5)
    r.add_argument("--pinned", action="store_true")
    r = sub.add_parser("recall"); r.add_argument("query"); r.add_argument("--top", type=int, default=5)
    r = sub.add_parser("forget"); r.add_argument("id"); r.add_argument("--hard", action="store_true")
    r = sub.add_parser("restore"); r.add_argument("id")
    r = sub.add_parser("explain"); r.add_argument("id")
    r = sub.add_parser("list"); r.add_argument("--status", default="active")
    sub.add_parser("archive"); sub.add_parser("stats")
    r = sub.add_parser("context"); r.add_argument("query"); r.add_argument("--max", type=int, default=2000)
    a = p.parse_args()

    ms = MemorySystem()
    if a.cmd == "remember":
        print(json.dumps(ms.remember(a.title, a.content, tags=a.tags.split(","),
              importance=a.importance, pinned=a.pinned), ensure_ascii=False))
    elif a.cmd == "recall":
        for h in ms.recall(a.query, top_k=a.top, explain=True):
            print(f"[S{h['tier']}] {h['title']}  ({h['reason']})\n  {h['content'][:80]}\n")
    elif a.cmd == "forget":
        print("已遗忘" if ms.forget(a.id, hard=a.hard) else "未找到该记忆")
    elif a.cmd == "restore":
        print("已恢复" if ms.restore(a.id) else "回收站中没有该记忆")
    elif a.cmd == "explain":
        print(json.dumps(ms.explain(a.id), ensure_ascii=False, indent=2))
    elif a.cmd == "list":
        for m in ms.list_memories(status=a.status):
            pin = "📌" if m["pinned"] else "  "
            print(f"{pin} S{m['tier']} [{m['importance']:.1f}] {m['title']}  "
                  f"(回忆{m['recalls']}次, {m['last_recalled']})  {m['id']}")
    elif a.cmd == "archive":
        print(json.dumps(ms.daily_archive(), ensure_ascii=False))
    elif a.cmd == "stats":
        print(json.dumps(ms.stats(), ensure_ascii=False, indent=2))
    elif a.cmd == "context":
        print(ms.build_context(a.query, max_chars=a.max))


if __name__ == "__main__":
    main()

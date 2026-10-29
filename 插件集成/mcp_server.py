# -*- coding: utf-8 -*-
"""
MCP 服务器（stdio 传输，零依赖手写 JSON-RPC）
============================================
把记忆系统作为标准 MCP 工具服务，供 Claude Desktop、Cline、Cursor、
Cherry Studio 等支持 MCP 的 Agent 直接接入。

提供工具：
  remember(title, content, importance, pinned)  — 保存记忆
  recall(query)                                 — 检索记忆（读原文）
  forget(keyword)                               — 遗忘（软删除）
  build_context(query)                          — 生成可注入的记忆上下文
  list_memories()                               — 列出所有记忆
  daily_archive()                               — 执行归档衰减

Claude Desktop 配置示例（claude_desktop_config.json）：
{
  "mcpServers": {
    "brain-memory": {
      "command": "python3",
      "args": ["/路径/插件集成/mcp_server.py"]
    }
  }
}
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_system import MemorySystem

MS = MemorySystem(root=os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory"))

TOOLS = [
    {"name": "remember",
     "description": "保存一条长期记忆（原文无损）。用于用户偏好、身份信息、项目约束、明确决定等稳定事实。",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "content": {"type": "string"},
         "importance": {"type": "number", "default": 0.6},
         "pinned": {"type": "boolean", "default": False}},
      "required": ["title", "content"]}},
    {"name": "recall",
     "description": "按自然语言检索记忆，命中后返回原文并自动强化该记忆。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}},
      "required": ["query"]}},
    {"name": "forget",
     "description": "按关键词找到并遗忘一条记忆（软删除，30天内可恢复）。",
     "inputSchema": {"type": "object", "properties": {
         "keyword": {"type": "string"}}, "required": ["keyword"]}},
    {"name": "build_context",
     "description": "生成与当前话题相关的记忆上下文文本，可直接拼入提示词。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "max_chars": {"type": "integer", "default": 2000}},
      "required": ["query"]}},
    {"name": "list_memories",
     "description": "列出所有活跃记忆的标题、层级、重要度和 id。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "daily_archive",
     "description": "执行每日归档：低强度记忆降层，跌破阈值的进回收站。",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call_tool(name, args):
    if name == "remember":
        r = MS.remember(args["title"], args["content"],
                        importance=args.get("importance", 0.6),
                        pinned=args.get("pinned", False), source="mcp")
        return json.dumps(r, ensure_ascii=False)
    if name == "recall":
        hits = MS.recall(args["query"], top_k=args.get("top_k", 5))
        return json.dumps(hits, ensure_ascii=False) if hits else "没有相关记忆"
    if name == "forget":
        hits = MS.recall(args["keyword"], top_k=1)
        if not hits:
            return "没有相关记忆"
        MS.forget(hits[0]["id"])
        return f"已遗忘：{hits[0]['title']}"
    if name == "build_context":
        return MS.build_context(args["query"], max_chars=args.get("max_chars", 2000)) \
               or "（无相关记忆）"
    if name == "list_memories":
        return json.dumps(MS.list_memories(), ensure_ascii=False)
    if name == "daily_archive":
        return json.dumps(MS.daily_archive(), ensure_ascii=False)
    raise ValueError(f"unknown tool: {name}")


def reply(rid, result):
    sys.stdout.write(json.dumps(
        {"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = msg.get("method"), msg.get("id")
        if method == "initialize":
            reply(rid, {"protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "brain-memory", "version": "1.0.0"}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            p = msg["params"]
            try:
                text = call_tool(p["name"], p.get("arguments", {}))
                reply(rid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                reply(rid, {"content": [{"type": "text", "text": f"error: {e}"}],
                            "isError": True})
        elif rid is not None:
            reply(rid, {})


if __name__ == "__main__":
    main()

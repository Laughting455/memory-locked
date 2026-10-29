# 插件集成总览

三种方式把记忆系统接入你的 AI，按场景选择：

## 1. AstrBot 插件（`memory-locked/`）

适合：QQ / 微信 / Telegram 等聊天机器人场景。

- 复制整个文件夹到 `AstrBot/data/plugins/`，WebUI 重载即可。
- 每轮对话自动检索记忆注入上下文；支持 `/记住`、`/永久记住`、`/忘掉`、`/回忆`、`/记忆列表` 指令；模型可通过内置工具自主记忆；每天自动归档。
- 详见插件内 README。

## 2. OpenAI 兼容封装（`openai_compatible_agent.py`）

适合：DeepSeek API、Ollama、LM Studio、vLLM 等任何 OpenAI 兼容接口。

```bash
export LLM_BASE_URL="https://api.deepseek.com/v1"   # 本地 Ollama 则用 http://localhost:11434/v1
export LLM_API_KEY="sk-xxx"                          # 本地可任意填
export LLM_MODEL="deepseek-chat"                     # Ollama 例：qwen2.5:7b
python3 openai_compatible_agent.py
```

- 每轮对话自动注入相关记忆，对话后自动提取新事实存入。
- 也可作为库：`from openai_compatible_agent import MemoryAgent`。

## 3. MCP 服务器（`mcp_server.py`）

适合：Claude Desktop、Cline、Cursor、Cherry Studio 等支持 MCP 标准的 Agent。

在客户端配置中加入（以 Claude Desktop 为例）：

```json
{
  "mcpServers": {
    "brain-memory": {
      "command": "python3",
      "args": ["/绝对路径/插件集成/mcp_server.py"]
    }
  }
}
```

重启客户端后，Agent 将获得 6 个工具：remember / recall / forget / build_context / list_memories / daily_archive，可以自主存取记忆。

## 数据互通

三个集成各自使用独立的数据目录。如需共享记忆，把 `memory/` 文件夹指向同一路径即可（MemorySystem 构造时传 `root=` 参数）。

## 目录

1. 五分钟快速上手
2. 核心概念：系统如何记忆与遗忘
3. 命令行完整用法
4. 接入 AI（Python API 与三种现成集成）
5. 自然语言指令约定
6. 调参指南
7. 数据安全与备份
8. 常见问题与故障排除

---

## 一、五分钟快速上手

### 1. 环境要求

Python 3.8 以上，**无需安装任何第三方库**。终端进入文件夹：

```bash
cd ai记忆系统
python3 memory_system.py --help     # Windows 上可能是 python
```

### 2. 存第一条记忆

```bash
python3 memory_system.py remember "咖啡偏好" "我只喝美式咖啡，不加糖不加奶。"
```

输出：

```json
{"id": "mem_1761..._4abb03bb97", "action": "created", "tier": 2}
```

`created` 表示新建成功，默认进入 S2 层。

### 3. 让它回忆

```bash
python3 memory_system.py recall "推荐点什么咖啡好"
```

注意你并没有用原句提问，系统通过内置中文分词匹配到了“咖啡”这条记忆，并且自动把它从 S2 提升到了 S1——**被想起的记忆会被强化**。

### 4. 把记忆给任何 AI 用

```bash
python3 memory_system.py context "推荐一些饮品" --max 1000
```

把输出整段复制，粘贴到 Kimi、ChatGPT、DeepSeek 等任意 AI 对话开头，附一句“以下是你关于我的长期记忆”，它就立刻“认识”你了。

### 5. 看一眼你的记忆库

```bash
python3 memory_system.py list      # 记忆清单
python3 memory_system.py stats     # 分层统计
```

至此你已经走通了 **存 → 取 → 用** 的完整流程。

---

## 二、核心概念：系统如何记忆与遗忘

### 记忆层级（模拟人脑的记忆深浅）

| 层级 | 名称 | 含义 | 衰减周期 |
|---|---|---|---|
| S0 | 工作记忆 | 当前对话内容（由对话本身承载，系统不存） | — |
| S1 | 核心记忆 | 钉住的或高活跃记忆，优先注入上下文 | 7 天 |
| S2 | 近期记忆 | 新写入记忆的默认层级 | 30 天 |
| S3 | 沉睡记忆 | 较久未被提起 | 90 天 |
| S4 | 冷记忆 | 接近遗忘 | 180 天 |
| — | 回收站 | 软删除，保留 30 天可恢复 | — |

### 记忆强度公式

```
强度 = 0.40 × 重要度 + 0.35 × 时间新近度 + 0.25 × 回忆频率
```

每天归档时重新计算每条记忆的强度：

- 强度 < 0.35 → **下降一层**（逐渐淡忘）
- S4 且强度 < 0.15 → **移入回收站**（软删除，30 天内可恢复）
- 回收站超过 30 天 → **物理删除**（真正遗忘）
- **钉住（pinned）的记忆永不衰减、永不自动删除**

### 三条关键设计

1. **原文无损**：记忆以 Markdown 原文保存，检索命中后读取原文注入上下文，标题和标签只用于检索，不作为事实依据——不会因摘要压缩而失真。
2. **重复即复习**：写入相同内容（SHA-256 哈希一致）不会重复存储，而是自动强化已有记忆：回忆次数 +1、提升一层。
3. **回忆有节制**：命中后最多只提升一层，避免某条记忆被频繁提及就永远霸占核心层（防止“记忆抖动”）。

### 事实变更：版本链

世界在变，记忆也会过时。用新版本取代旧记忆，而不是覆盖：

```python
old = ms.recall("住址")[0]
ms.remember("用户住址", "用户现住北京市朝阳区。", importance=0.6, supersedes=old["id"])
```

旧记忆标记为 `superseded`：不再被检索、不再注入上下文，但保留在库中可追溯历史。

---

## 三、命令行完整用法

### remember —— 写入记忆

```bash
# 普通记忆（默认 S2 层，重要度 0.5）
python3 memory_system.py remember "用户住址" "用户住在上海市徐汇区。"

# 带标签和重要度（标签可提升检索命中率）
python3 memory_system.py remember "项目交付约束" "上线前必须完成安全审计。" \
    --tags 项目,交付 --importance 0.7

# 钉住：永久记住，适合过敏史、身份信息等关键事实
python3 memory_system.py remember "花生过敏" "用户对花生严重过敏。" --pinned --importance 1.0
```

**重要度参考**：1.0 = 性命攸关（过敏、身份）；0.7 = 重要约束（项目规则）；0.5 = 一般偏好；0.2 = 闲聊琐事。

### recall —— 回忆

```bash
python3 memory_system.py recall "为什么项目不能直接上线"
python3 memory_system.py recall "咖啡" --top 3
```

- 支持中文整句、零散关键词、中英混合（内置中文二元分词器）
- 输出标注命中原因（BM25 得分、原层级），每次命中自动强化该记忆

### list / stats / explain —— 查看与审计

```bash
python3 memory_system.py list                    # 活跃记忆清单
python3 memory_system.py list --status trashed   # 回收站
python3 memory_system.py stats                   # 各层数量、复习总次数
python3 memory_system.py explain <id>            # 某条记忆的完整档案
```

`explain` 输出：创建时间、来源、最近回忆时间、回忆次数、重要度、层级，以及**每次是被什么问题唤起的**——回答“AI 你为什么记得这个？”

### forget / restore —— 遗忘与恢复

```bash
python3 memory_system.py forget <id>           # 软删除，30 天内可恢复
python3 memory_system.py restore <id>          # 从回收站找回
python3 memory_system.py forget <id> --hard    # 立即物理删除，不可恢复
```

### archive —— 每日归档

```bash
python3 memory_system.py archive
```

输出如 `{"demoted": [...], "trashed": [...], "purged": [], "kept": 5}`。建议加入定时任务每天执行：

```bash
# Linux / macOS
0 3 * * * cd /path/to/ai记忆系统 && python3 memory_system.py archive
```

### context —— 生成记忆上下文

```bash
python3 memory_system.py context "推荐一些零食" --max 2000
```

注入优先级：**钉住记忆 > S1 核心记忆 > 本轮检索命中**，严格按字数预算截断，装不下时低优先级的让位。

---

## 四、接入 AI

### 方式 A：现成集成（推荐，见 `插件集成/README.md`）

| 集成 | 文件 | 适用场景 |
|---|---|---|
| AstrBot 插件 | `插件集成/astrbot_plugin_brain_memory/` | QQ/微信/Telegram 机器人，支持 `/记住` 等聊天指令、自动注入、模型自主记忆 |
| OpenAI 兼容封装 | `插件集成/openai_compatible_agent.py` | DeepSeek API、Ollama、LM Studio 等，三行配置即用 |
| MCP 服务器 | `插件集成/mcp_server.py` | Claude Desktop、Cline、Cursor 等标准 Agent |

### 方式 B：自己写代码（Python API）

```python
from memory_system import MemorySystem

ms = MemorySystem()

def chat(user_input, history):
    # 1. 检索记忆（核心记忆 + 本轮回忆，受字数预算控制）
    memory_ctx = ms.build_context(user_input, max_chars=2000)
    messages = [
        {"role": "system", "content":
            f"你是助手。以下是你关于用户的长期记忆：\n{memory_ctx}"},
        *history,
        {"role": "user", "content": user_input},
    ]
    reply = call_llm(messages)

    # 2. 让 LLM 提取本轮值得记住的事实（JSON 数组）
    extraction = call_llm([{"role": "user", "content":
        ms.extract_prompt(f"用户：{user_input}\n助手：{reply}")}])
    for fact in json.loads(extraction):
        ms.remember(fact["title"], fact["content"], tags=fact["tags"],
                    importance=fact["importance"],
                    pinned=fact.get("pinned", False), source="auto")
    return reply
```

### 用户反馈强化

```python
ms.feedback(memory_id, useful=True)   # “这条记对了” → 重要度 +0.1
ms.feedback(memory_id, useful=False)  # “这不重要”  → 重要度 -0.1
```

---

## 五、自然语言指令约定

接入对话后，可以直接对 AI 说：

| 你说 | 系统行为 |
|---|---|
| “记住这个……” | `remember()`，重要度 0.7 |
| “永久记住……” | `remember()`，`pinned=True`，永不遗忘 |
| “忘掉……” | `forget()` 软删除 |
| “你为什么记得这个？” | `explain()` 输出来源、时间、复习历史 |
| “这个不重要” | `feedback(useful=False)` |

---

## 六、调参指南

`memory_system.py` 顶部的四个参数：

```python
TIER_TAU = {1: 7, 2: 30, 3: 90, 4: 180}  # 各层衰减周期（天），层级越深越抗遗忘
TRASH_RETENTION_DAYS = 30                 # 回收站保留天数
DEMOTE_THRESHOLD = 0.35                   # 降层阈值（调大 = 忘得快）
FORGET_THRESHOLD = 0.15                   # 遗忘阈值（调大 = 删得多）
```

- 想让 AI **记忆力强**：调大 `TIER_TAU`，调小两个阈值
- 想让 AI **轻装上阵**：调小 `TIER_TAU`，调大两个阈值
- 修改后无需迁移数据，下次归档自动生效

---

## 七、数据安全与备份

所有数据都在本地，结构透明：

```
memory/
├── raw/YYYY-MM-DD/*.md   # 记忆原文（可直接阅读、手工编辑）
├── trash/                # 回收站
└── index.db              # SQLite 索引（元数据 + 全文检索）
```

- **备份**：整体复制 `memory/` 文件夹即可；恢复时原样放回。
- **重置**：删除整个 `memory/` 文件夹，下次运行自动重建空库。
- **隐私**：纯本地存储，无任何网络请求；接入 LLM 时只有被检索命中的记忆会随请求发出。

---

## 八、常见问题与故障排除

**Q：记忆会失真吗？**
不会。原文无损保存，检索命中后读原文。摘要、标签、分词仅用于检索，永远不作为事实依据。

**Q：相同内容写两次会怎样？**
自动去重：第二次写入转为“复习强化”，回忆次数 +1、提升一层，不会产生重复条目。

**Q：recall 搜不到明明存过的内容？**
内置分词按中文二元组匹配，问法与原文差异过大时可能漏检。缓解方法：写入时加 `--tags` 补充同义词；或换用原文中出现过的词。这是当前版本的主要局限，未来可接向量检索。

**Q：上下文装不下怎么办？**
`build_context(max_chars=N)` 严格按预算截断，按“钉住 > S1 > 检索命中”的顺序取舍。

**Q：归档后记忆“消失”了？**
先查回收站：`list --status trashed`，在的话用 `restore <id>` 找回。找回后建议 `--pinned` 或调高重要度，防止再次沉底。

**Q：Windows 上命令报错？**
把 `python3` 换成 `python`；路径含中文或空格时用引号包裹。

**Q：如何让多套集成共享同一个记忆库？**
构造时传同一个路径：`MemorySystem(root="/共享路径/memory")`。

---


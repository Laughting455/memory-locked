# -*- coding: utf-8 -*-
"""
OpenAI 兼容 API 的记忆封装
==========================
一行接入任何 OpenAI 兼容接口：DeepSeek、Ollama、LM Studio、vLLM、Kimi 等。
每轮对话自动检索并注入记忆，可选自动提取新记忆。

用法（DeepSeek）：
    agent = MemoryAgent(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-xxx",
        model="deepseek-chat",
    )
    print(agent.chat("推荐一家餐厅"))

用法（本地 Ollama）：
    agent = MemoryAgent(
        base_url="http://localhost:11434/v1",
        api_key="ollama",          # 本地接口通常任意填
        model="qwen2.5:7b",
    )
    print(agent.chat("我喜欢吃什么"))
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_system import MemorySystem


class MemoryAgent:
    def __init__(self, base_url, api_key, model,
                 memory_root=None, context_budget=2000,
                 auto_extract=True, system_prompt="你是一个乐于助人的助手。"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.ms = MemorySystem(root=memory_root or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "memory"))
        self.context_budget = context_budget
        self.auto_extract = auto_extract
        self.system_prompt = system_prompt
        self.history = []          # 当前会话历史（S0 工作记忆）

    def chat(self, user_input):
        # 1. 回忆：检索相关记忆
        memory_ctx = self.ms.build_context(user_input, max_chars=self.context_budget)
        system = self.system_prompt
        if memory_ctx:
            system += "\n\n# 关于用户的长期记忆\n" + memory_ctx

        messages = [{"role": "system", "content": system},
                    *self.history[-20:],                    # 保留最近 10 轮
                    {"role": "user", "content": user_input}]
        reply = self._call(messages)
        self.history += [{"role": "user", "content": user_input},
                         {"role": "assistant", "content": reply}]

        # 2. 记忆：让模型提取值得长期保存的事实
        if self.auto_extract:
            self._extract(user_input, reply)
        return reply

    def _extract(self, user_input, reply):
        try:
            prompt = self.ms.extract_prompt(f"用户：{user_input}\n助手：{reply}")
            raw = self._call([{"role": "user", "content": prompt}], max_tokens=800)
            start, end = raw.find("["), raw.rfind("]")
            if start == -1:
                return
            for fact in json.loads(raw[start:end + 1]):
                self.ms.remember(fact["title"], fact["content"],
                                 tags=fact.get("tags", []),
                                 importance=fact.get("importance", 0.6),
                                 pinned=fact.get("pinned", False),
                                 source="auto")
        except Exception:
            pass                   # 提取失败不影响对话

    def _call(self, messages, max_tokens=2000):
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps({"model": self.model, "messages": messages,
                             "max_tokens": max_tokens}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]

    # 常用管理操作
    def remember(self, content, pinned=False):
        return self.ms.remember(title=content[:20], content=content,
                                importance=0.9 if pinned else 0.7, pinned=pinned)

    def forget(self, keyword):
        hits = self.ms.recall(keyword, top_k=1)
        if hits:
            self.ms.forget(hits[0]["id"])
            return f"已遗忘：{hits[0]['title']}"
        return "没有相关记忆"

    def daily_archive(self):
        return self.ms.daily_archive()

    def new_session(self):
        """开始新会话（清空工作记忆，长期记忆保留）"""
        self.history = []


if __name__ == "__main__":
    # 交互式 demo：python3 openai_compatible_agent.py
    agent = MemoryAgent(
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ.get("LLM_MODEL", "deepseek-chat"))
    print("记忆助手已启动。输入 /记住 xxx、/忘掉 xxx、/新会话、/退出")
    while True:
        text = input("你: ").strip()
        if text in ("/退出", "/quit"):
            break
        elif text.startswith("/记住"):
            print(agent.remember(text[3:].strip()))
        elif text.startswith("/忘掉"):
            print(agent.forget(text[3:].strip()))
        elif text == "/新会话":
            agent.new_session(); print("已开始新会话")
        elif text:
            print("AI:", agent.chat(text))

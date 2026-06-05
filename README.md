# recodex

[English](README.en.md) | 中文

> 复盘最近一次 Codex 会话，看看下一次哪里可以用得更好。

`recodex` 是一个本地优先 CLI。它读取本地 Codex session transcript，分析你这次是怎么使用 Codex 的，并默认打开一份本地静态 HTML 报告。

它帮助你发现：

- 哪些上下文给得太晚
- 任务边界在哪里发生了漂移
- 哪些时刻更早暂停、纠偏或拆分会更好
- 收尾是否缺少验证证据
- 哪些项目事实应该在下一次会话前写入文档

它不是 transcript viewer，不是 prompt optimizer，也不是泛用 AI 总结器。

它复盘的是一次 Codex session 周围的使用流程。

```bash
recodex
```

```text
[ok] Found latest Codex session
[ok] Quick analysis completed
[ok] Generated report.html
[ok] Opened report in browser

Key findings:
- 关键上下文补充偏晚
- 任务边界略有漂移
- 收尾缺少验证证据
```

![recodex HTML report](docs/assets/report-page-screenshot.png)

---

## 为什么

用好 Codex 不只是模型能力问题。

一次混乱的 AI 编程会话，通常不是因为“AI 不够聪明”，而是流程闭环不稳：

- 任务开始时上下文不够
- 关键项目事实出现太晚
- 调试、重构、部署、文档混在同一个 session
- AI 在错误方向上继续探索
- 最终回答说完成，但没有测试、构建、typecheck、lint 或手动验证证据
- 用户反复解释同一个项目事实

`recodex` 的目标很窄：从真实 Codex 会话里提炼下一次更高效使用 AI coding agent 的具体改进点。

---

## 分析什么

`recodex` 关注五件事：

- **任务启动**：目标、上下文、约束、完成条件是否清楚
- **上下文时机**：哪些信息出现太晚，是否导致无效探索
- **过程干预**：什么时候应该暂停、总结假设、纠偏或拆分 session
- **验证和验收**：是否有可审查的验证命令和结果
- **可复用改进**：哪些事实、流程、命令可以沉淀到文档、checklist、script、hook、CI 或 skill

---

## 会生成什么

默认生成本地静态报告：

```text
.recodex/reports/<session-id>/
  report.html
  report.json
  report.md
```

`report.html` 是单文件 HTML。结构化 JSON 会嵌入页面内部：

```html
<script id="report-data" type="application/json">...</script>
```

页面不扫描 Codex session，也不运行时 fetch 外部 JSON。CLI 先解析和分析，再渲染页面。

报告包含：

1. 概览
2. 流程路径
3. 主要问题
4. 上下文前置分析
5. 过程干预分析
6. 验证和验收
7. 可执行建议
8. 证据附录

![Report anatomy](docs/assets/report-anatomy.svg)

---

## 快速开始

从源码运行：

```bash
git clone <repo-url>
cd recodex
uv sync
uv run recodex
```

常用方式：

```bash
recodex              # 分析 latest session 并打开 HTML 报告
recodex --no-open    # 生成报告但不打开浏览器
recodex --terminal   # 保持浏览器关闭，只看终端摘要
recodex --json       # 只生成 report.json
```

显式 latest：

```bash
recodex latest
recodex latest --since 30d
```

---

## 命令

常用命令：

```bash
recodex              # 分析最新 session 并打开 HTML 报告
recodex latest       # 显式 latest-session 分析
recodex open latest  # 重新打开最近生成的报告
recodex history      # 汇总最近会话里的重复模式
recodex doctor       # 检查 Codex session 目录和 recodex 状态
```

高级命令：

```bash
recodex scan ~/.codex/sessions
recodex report latest --open
recodex retro latest --local-only
recodex quickstart --since 7d --limit 5
recodex history --since 30d
recodex export agents
recodex export checklist
recodex storage stats
```

`quickstart` 是显式多会话流程：它会按项目聚合最近会话，生成项目报告和改进资产。它不是默认入口。

---

## 可执行建议

`recodex` 可能建议后续动作，例如：

- 把项目命令写入 `AGENTS.md`
- 增加完成前 checklist
- 把重复命令转成脚本
- 增加 hook 或 CI 检查
- 为重复流程创建 skill

建议不会自动应用。先 review，再落地。

---

## 可选本地报告服务

默认情况下，`recodex` 生成自包含的 `report.html` 并用浏览器打开，不需要后台服务。

本地 report server 适合以后浏览多份报告、搜索历史报告、查看周报和趋势。这个能力是可选增强，不是默认入口。

计划命令：

```bash
recodex serve
```

---

## 隐私

`recodex` 是本地优先设计：

- 只读本地 Codex transcript
- 不修改原始 Codex session 文件
- 默认把报告写到本地 `.recodex`
- LLM 分析默认关闭
- 可选 LLM 分析前会先脱敏
- 支持本地确定性分析

脱敏范围包括 API keys、tokens、`.env` 内容、database URLs、cookies、private keys、Authorization headers、home path 和 emails。

---

## 可选 LLM 分析

LLM 是 opt-in。默认路径是本地确定性解析、规则经验库匹配和启发式建议。

测试 LLM 链路：

```bash
recodex retro latest --llm --llm-provider mock
```

OpenAI：

```bash
export OPENAI_API_KEY=...
recodex retro latest --llm --allow-cloud
```

火山方舟 / 豆包：

```bash
export ARK_API_KEY=...
recodex retro latest --llm --llm-provider volcengine --allow-cloud
```

或写入 `~/.recodex/config.toml`：

```toml
[analysis]
local_only = false
llm_provider = "volcengine"
llm_api_key_env = "ARK_API_KEY"
# llm_model = "doubao-seed-2-0-lite-260215"
```

---

## 配置

项目配置：`.recodex.toml`

```toml
[sources.codex]
enabled = true
sessions_dir = "~/.codex/sessions"

[privacy]
redact_secrets = true
redact_env_files = true
redact_home_path = true

[analysis]
local_only = true

[outputs]
reports_dir = "./.recodex/reports"
```

全局配置：`~/.recodex/config.toml`

---

## Roadmap

当前聚焦：

- [x] 分析 latest Codex session
- [x] 生成单文件 HTML 报告
- [x] 检测上下文补充偏晚
- [x] 检测验证证据缺失
- [x] 生成 Top 建议和证据附录

下一步：

- [ ] 更好的 evidence appendix
- [ ] `recodex open` 历史报告选择
- [ ] `recodex doctor` 大 session 目录诊断
- [ ] AGENTS.md suggestion snippets
- [ ] checklist suggestions
- [ ] 可选本地 report server

更后面：

- [ ] deep analysis mode
- [ ] batch analysis
- [ ] eval suite
- [ ] Claude Code adapter
- [ ] Cursor adapter
- [ ] Git / GitHub adapter
- [ ] CI logs adapter

---

## FAQ

### 这是 prompt optimizer 吗？

不是。它可能发现某些信息应该更早给到 Codex，但核心不是改写 prompt。

它复盘的是使用流程：上下文、任务边界、干预时机、验证闭环和可复用改进。

### 它会判断最终代码是否正确吗？

不会。它检查的是 session 是否产生了足够的验证证据。

如果 AI 改了代码但没有测试、构建、typecheck、lint 或手动验证，报告会降低完成可信度。

### 它会上传我的 Codex sessions 吗？

默认不会。

默认路径是本地确定性分析。启用 LLM 分析时，工具发送的是脱敏后的紧凑分析包，而不是完整原始 transcript。

### 为什么默认生成 HTML？

终端适合快速摘要，但不适合阅读结构化复盘。

HTML 更适合浏览、保存、分享、打印，也适合附到 issue 或笔记里。

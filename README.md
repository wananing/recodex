# recodex

[English](README.en.md) | 中文

<p align="center">
  <img src="docs/assets/recodex-promo-hero.jpg" alt="recodex 把本地 AI 编程会话转成提效报告和可复用项目知识" width="100%">
</p>

<p align="center">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-10b981">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-2563eb">
  <img alt="Dashboard first" src="https://img.shields.io/badge/workflow-Dashboard--first-f59e0b">
  <img alt="LLM required" src="https://img.shields.io/badge/report-LLM--backed-ef4444">
</p>

> 把真实 AI 编程会话复盘成下一次更高效协作的提效报告。

`recodex` 是一个本地优先的 AI 编程会话复盘工具。它读取本机 Codex、Claude Code 或 Cursor 会话记录，在用户配置的大模型上生成一份协作提效报告，帮助你判断下一次应该如何更清楚地给上下文、拆任务、做验证和沉淀项目经验。

报告分析的主体是使用 AI 编程工具的开发者，而不是评价 AI 是否“聪明”。它关注：

- 哪些目标、约束或验收标准应该更早给出
- 哪些来回沟通、失败探索或纠偏可以避免
- 哪些项目知识适合沉淀到 `AGENTS.md`、检查清单、脚本或 skill
- 下次开启类似任务时应该直接怎么做

## 为什么值得用

- **复盘真实会话**：从本地 Codex、Claude Code、Cursor 聊天记录里找协作摩擦。
- **面向下一次提效**：报告不评价模型，而是告诉你下次怎么给上下文、拆任务、验收结果。
- **本地优先**：原始会话保留在本机，只把脱敏后的必要分析包交给你配置的 LLM。
- **能沉淀资产**：把反复出现的问题转成项目文档、检查清单、脚本或 skill 候选。

## 产品预览

<p align="center">
  <img src="docs/assets/recodex-promo-report.jpg" alt="recodex 报告页面展示提效问题、聊天依据和验收证据" width="100%">
</p>

<p align="center">
  <img src="docs/assets/recodex-promo-workflow.jpg" alt="recodex 从本地会话到脱敏分析再到提效报告的工作流" width="100%">
</p>

## 快速开始

```bash
git clone <repo-url>
cd recodex
uv sync
make dashboard-install
make dashboard-build
make dashboard-serve
```

打开 Dashboard 后按这个顺序使用：

1. 在首页或导入页导入本地会话。
2. 在 `LLM` 页面配置 Provider、Model、Base URL 和 API Key。
3. 回到首页选择项目和会话。
4. 点击“生成提效报告”。
5. 在“报告”菜单查看历史报告，点击进入新版报告页。

## 唯一报告

当前产品只保留一种用户可见报告：**会话提效报告**。

报告生成需要 LLM。没有启用 LLM Provider 时，Dashboard 会直接提示先配置模型，不生成报告。

报告包含：

- 报告重点：本次最值得改进的协作问题
- 下一次怎么做：按优先级给出的具体动作
- 聊天依据：只基于聊天文字提取的观察，不把工具执行结果当成聊天结论
- 提效问题证据：成本、根因和证据引用
- 沉淀建议：适合写入文档、清单、脚本或 skill 的内容
- 验收证据：区分已验证和只是声称完成

生成文件默认写入本地 `.recodex/reports`，包括 HTML、Markdown 和 JSON。

## 常用命令

```bash
make dashboard-serve
PYTHONPATH=src python3 -m recodex serve --dashboard-dir dashboard/dist
PYTHONPATH=src python3 -m recodex scan ~/.codex/sessions
PYTHONPATH=src python3 -m recodex doctor
```

需要自动化时，可以使用同一份 LLM 提效报告的 headless 入口：

```bash
PYTHONPATH=src python3 -m recodex report latest --llm --llm-provider volcengine --allow-cloud
```

日常使用以 Dashboard 首页生成报告为准。旧的 `latest`、`quickstart`、`retro`、`patterns`
等本地报告命令已退休，只会输出迁移提示。

## LLM 配置

Dashboard 支持这些 Provider 预设：

- Volcengine Ark / Doubao
- DashScope / Qwen
- SiliconFlow / DeepSeek
- OpenAI Responses
- OpenAI-compatible API

示例环境变量：

```bash
export ARK_API_KEY=...
export OPENAI_API_KEY=...
```

用户自己控制 Provider 和 Key。`recodex` 不提供托管后端。

## 隐私

`recodex` 默认读写本地文件：

- 只读原始会话记录，不修改原始文件
- 报告和数据库写入本地 `.recodex`
- 报告生成前会进行脱敏
- API keys、tokens、`.env`、database URLs、cookies、private keys、Authorization headers、home path 和 emails 会被处理

LLM 报告会发送必要的脱敏分析包。聊天记录分析以用户和助手的文字消息为主，不把工具执行结果作为聊天结论。

## 开发

```bash
make test
make dashboard-build
make build
```

核心 Python 代码在 `src/recodex/`，Dashboard 在 `dashboard/src/`，测试在 `tests/`。

## 维护与贡献

- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 更新记录：[CHANGELOG.md](CHANGELOG.md)
- 安全说明：[SECURITY.md](SECURITY.md)
- 维护边界：[docs/maintenance.md](docs/maintenance.md)
- 开源协议：[MIT License](LICENSE)

## 宣传素材

- README 主视觉：[docs/assets/recodex-promo-hero.jpg](docs/assets/recodex-promo-hero.jpg)
- 报告展示图：[docs/assets/recodex-promo-report.jpg](docs/assets/recodex-promo-report.jpg)
- 工作流展示图：[docs/assets/recodex-promo-workflow.jpg](docs/assets/recodex-promo-workflow.jpg)
- GitHub social preview：[docs/assets/recodex-social-preview.jpg](docs/assets/recodex-social-preview.jpg)

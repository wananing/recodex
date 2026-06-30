# recodex 固定问题分类与效率改进体系设计

**版本：** v2.0 设计稿
**状态：** 可进入产品与工程评审
**适用对象：** 产品、架构、LLM 工程、数据工程、报告设计、开源协作者
**文档目标：** 定义 recodex 如何稳定分析庞大的 Codex 会话，从固定效率问题类型中识别问题、计算可观察成本、判断根因，并把改进路由到 `AGENTS.md`、Skill、Checklist、Script、Hook、CI、项目文档或其他机制。

---

## 0. 执行摘要

recodex 的两个核心能力是：

1. **效率问题诊断**
   从真实会话中识别人和 Codex 协作时反复发生的低效模式，重点关注重复要求、重复探索、失败循环、任务漂移、监督成本、验证债务和环境摩擦。

2. **改进沉淀路由**
   判断这些问题是否值得长期沉淀，以及最合适的载体是什么：`AGENTS.md`、路径规则、Skill、Script、Checklist、Hook、CI、项目文档、MCP 集成，或者只给一次性使用建议。

核心分析原则是：

> 固定的是“问题类型”和“解决机制”，变化的是每次会话中的证据、成本、根因和最终建议。

因此，LLM 不负责自由点评整份聊天记录，而承担四个受约束角色：

```text
问题分类器
+ 语义归因器
+ 解决机制选择器
+ 自然语言报告生成器
```

程序负责：

```text
日志解析
事实统计
行为序列识别
重复次数计算
成本账本
证据引用
Schema 校验
最终审计
```

完整主链路：

```text
原始 Session
→ 标准化事件
→ Episode 分段
→ 确定性信号
→ 固定问题类型分类
→ 可观察成本
→ 根因与责任层
→ 改进机制路由
→ 跨 Session 稳定性判断
→ Artifact Candidate
→ report.json
→ report.html
```

---

# 第一章：产品重新定义

## 1.1 recodex 不是什么

recodex 不是：

- 聊天记录查看器；
- 普通会话摘要器；
- Prompt 改写器；
- “AI 做得好不好”的简单评分器；
- 用一堆最佳实践批评用户的审计工具；
- 自动把每个问题都生成 Skill 的工具。

## 1.2 recodex 是什么

recodex 是一个：

> **AI 编程协作效率诊断与改进沉淀系统。**

它要回答：

- 这次会话中，最大的可避免成本是什么？
- 哪些要求被用户反复说明？
- 哪些项目知识被 Codex 反复重新发现？
- 哪些操作流程不断由用户手工编排？
- 哪些失败没有帮助缩小问题范围？
- 用户什么时候干预过晚或过密？
- 哪些验证成本被转移给了用户？
- 最高杠杆的改进动作是什么？
- 这个改进应该沉淀成什么？

## 1.3 两个核心能力的边界

### 核心一：效率问题诊断

输入：

```text
一个 Session
或同一项目的一组 Sessions
```

输出：

```text
Efficiency Findings
```

每个 Finding 包含：

- 固定问题类型；
- 会话证据；
- 发生次数；
- 可观察成本；
- 根因；
- 责任层；
- 改进建议；
- 置信度。

### 核心二：改进沉淀路由

输入：

```text
Audited Efficiency Findings
+ 跨 Session 重复性
+ 项目现有配置
```

输出：

```text
Artifact Candidates
```

可能的落点：

- 一次性 Coaching；
- 全局个人偏好；
- `AGENTS.md`；
- Path-scoped Rule；
- 项目文档；
- Checklist；
- Script；
- Hook；
- CI；
- Skill；
- Task Template；
- MCP / API 集成；
- 不沉淀。

---

# 第二章：分析哲学

## 2.1 以“可避免成本”为中心

错误思路：

```text
会话没有跑测试
→ 违反最佳实践
→ 建议下次跑测试
```

正确思路：

```text
存在代码修改
→ 会话没有验证结果
→ 用户在会话结束后重新打开项目并手工检查
→ 验证成本被转移给用户
→ 同类情况最近 5 次会话出现 3 次
→ 根因是项目没有统一完成标准
→ 建议写入 AGENTS.md；若必须强制执行，则增加 Hook / CI
```

## 2.2 探索和失败不天然等于低效

必要探索可能包括：

- 理解陌生项目结构；
- 验证多个合理假设；
- 读取核心调用链；
- 运行失败测试确认问题；
- 比较多个方案。

低效探索的特征是：

- 重复寻找相同信息；
- 读取结果没有进入后续决策；
- 已经确认错误方向后继续深入；
- 没有阶段性结论；
- 搜索范围不断扩大但问题空间没有缩小。

好的失败：

```text
失败
→ 排除假设
→ 缩小搜索空间
→ 下一步发生实质变化
```

低效失败：

```text
失败
→ 不理解结果
→ 使用近似方法再次尝试
→ 相同失败
```

## 2.3 分析的是协作系统，不是单一责任方

同一问题可能来自：

```text
Operator      用户表达、干预、接受结果的方式
Agent         Codex 的计划、探索、工具使用、推理与收尾行为
Project       项目结构、文档、脚本、测试入口、架构边界
Harness       AGENTS.md、Skills、Hooks、MCP、权限与自动化配置
Environment   Sandbox、构建、CI、依赖、外部平台与网络
```

报告中的措辞应优先使用：

```text
“这次协作中……”
“当前项目缺少……”
“这个信息更适合由 Harness 自动提供……”
```

而不是简单写：

```text
“用户没有……”
“Codex 做错了……”
```

## 2.4 规则经验库是诊断支持，不是分析起点

推荐顺序：

```text
先从轨迹中发现摩擦
→ 计算成本和重复性
→ 判断根因
→ 调用规则经验库寻找解释和解决机制
→ 回到原始证据确认
```

不推荐：

```text
读取所有规则
→ 检查会话违反了哪些规则
```

---

# 第三章：固定效率问题分类体系

## 3.1 分类体系总览

建议第一版定义 12 个稳定的问题大类：

| 编号 | 内部类型 | 用户可见含义 |
|---|---|---|
| E01 | `repeated_user_requirement` | 相同要求被用户反复说明 |
| E02 | `project_knowledge_rediscovery` | 稳定项目知识被反复重新发现 |
| E03 | `repeated_workflow_orchestration` | 固定多步骤流程被反复手工指导 |
| E04 | `repeated_command_sequence` | 固定命令序列被重复手工执行 |
| E05 | `redundant_exploration` | 探索范围过宽、重复或无贡献 |
| E06 | `hypothesis_stagnation` | 失败后假设没有更新 |
| E07 | `ignored_tool_evidence` | 工具输出未进入下一步决策 |
| E08 | `scope_drift` | 任务范围漂移或过度实现 |
| E09 | `intervention_mismatch` | 用户干预过晚或过密 |
| E10 | `context_handoff_loss` | 会话过长、任务混杂或交接成本高 |
| E11 | `verification_debt` | 验证债务、假完成或监督成本转移 |
| E12 | `environment_integration_friction` | 权限、环境和外部系统摩擦 |

用户报告不展示 `E01` 等编号。编号只服务于：

- 工程实现；
- Prompt 约束；
- Eval；
- 跨版本统计；
- 规则升级。

---

## 3.2 E01：相同要求被用户反复说明

### 定义

同一要求、限制、偏好或输出格式，在同一 Session 或多个 Session 中由用户重复表达。

### 典型例子

```text
“使用 pnpm，不要用 npm”
“不要改公共 API”
“只做最小修改，不要顺便重构”
“最终回答必须列出测试结果”
“先看 AGENTS.md”
```

### 确定性 / 语义信号

- 用户消息中出现否定性纠正；
- 相同主题的约束多次出现；
- Codex 执行后用户再次重复原要求；
- 多个 Session 出现语义相近的用户断言；
- 用户使用“又”“刚才说过”“不要再”等词。

### 可观察成本

- 用户纠正次数；
- 额外交互轮次；
- 因违反要求产生的撤销修改；
- 同一约束跨 Session 重复出现次数；
- 用户监督负担。

### 常见根因

- 项目稳定规则未文档化；
- 个人偏好未持久化；
- 路径级约束放在了错误层级；
- Agent 未读取已有规则；
- 要求仅存在于历史聊天中。

### 解决机制路由

| 要求类型 | 推荐机制 |
|---|---|
| 个人跨项目偏好 | 全局个人指令 |
| 项目稳定规则 | `AGENTS.md` |
| 目录局部规则 | Path-scoped Rule |
| 多步骤专项要求 | Skill / Checklist |
| 只在本任务有效 | Coaching，不沉淀 |

### 防误报

- 用户重复是为了强调风险，不一定意味着系统问题；
- 相似措辞未必是同一规则；
- 一次性业务约束不能直接升级为长期规则。

---

## 3.3 E02：稳定项目知识被反复重新发现

### 定义

每个新 Session 都重新探索同一个项目事实，而这些事实本可以被长期保存或自动提供。

### 典型例子

```text
测试命令
构建命令
项目入口
主要模块目录
服务名称
部署路径
数据库迁移方式
生成文件目录
架构边界
```

### 识别信号

- 多个 Session 搜索相同关键词；
- 多次读取 `package.json`、`Makefile`、CI 配置来找同一命令；
- 用户重复告诉 Codex 相同项目路径；
- 多次探索得到相同结论；
- 首次有效动作前存在大量重复导航行为。

### 可观察成本

- 启动轮次；
- 重复文件读取；
- 重复搜索；
- 错误目录操作；
- 首次有效修改或验证的延迟。

### 根因

- `AGENTS.md` 缺失；
- 项目文档分散或过时；
- 自动发现能力不足；
- Session 无跨会话上下文；
- 当前 Harness 没有注入项目元信息。

### 推荐机制

```text
AGENTS.md
项目开发文档
自动项目摘要
Task Runner / 命令目录
MCP / Project Metadata Provider
```

### 防误报

- 第一次接触陌生项目的探索是必要成本；
- 项目最近发生结构变化时，重新发现是合理的；
- 如果事实可以从仓库轻松发现，不应全部归因于用户未提供。

---

## 3.4 E03：固定多步骤流程被反复手工指导

### 定义

相似任务中，用户反复说明相同的操作顺序、判断步骤和验证要求。

### 典型例子

```text
部署：构建 → 备份 → 上传 → 重启 → 日志 → 健康检查
Bugfix：复现 → 定位 → 最小修复 → 相关测试 → Diff 检查
发布：版本号 → Changelog → 构建 → Tag → Release
```

### 识别信号

- 多 Session 出现相似工具调用序列；
- 用户多次说明同样的先后顺序；
- 流程中存在条件判断；
- 某一步经常漏掉并被用户补充；
- 流程有明确触发场景、输入和完成条件。

### 可观察成本

- 重复编排轮次；
- 漏步骤风险；
- 用户持续监督；
- 执行方式不一致；
- 不同 Session 产生不同结果。

### 推荐机制

| 流程特点 | 推荐机制 |
|---|---|
| 多步骤、有判断、需要解释 | Skill |
| 步骤固定、确定性强 | Script / Task Runner |
| 必须在生命周期自动执行 | Hook |
| 团队级质量门禁 | CI |
| 只是提醒步骤 | Checklist |

### 防误报

- 两个流程步骤相似，不代表它们是同一个业务流程；
- 一次性事故处理流程不能直接升级为 Skill；
- Skill 必须有稳定触发场景和可复用价值。

---

## 3.5 E04：固定命令序列被重复手工执行

### 定义

一组没有复杂判断、参数变化很小的命令，在多个 Session 中被重复生成和执行。

### 例子

```text
cd apps/web
pnpm install
pnpm typecheck
pnpm test:auth
git diff --check
```

### 识别方式

先把命令标准化：

```text
去除时间戳
归一化路径
抽取命令名与参数类型
忽略随机 ID
保留关键参数
```

再生成动作 Token：

```text
CHANGE_DIR
INSTALL_DEPS
TYPECHECK
RUN_AUTH_TESTS
CHECK_DIFF
```

跨 Session 查找高频子序列。

### 可观察成本

- 命令生成轮次；
- 拼写和路径错误；
- 重复 Token；
- 执行不一致；
- 用户每次确认参数。

### 推荐机制

```text
shell script
Makefile target
package.json script
task runner
```

如果必须在特定节点执行：

```text
Hook / CI
```

---

## 3.6 E05：探索范围过宽、重复或无贡献

### 定义

Codex 进行了大量文件读取、搜索和工具调用，但这些活动没有显著推动最终决策。

### 识别信号

- 重复读取未变化文件；
- 相同或高度相似的搜索查询；
- 已确认入口后仍继续全仓搜索；
- 大量文件没有进入计划、修改或证据；
- 工具调用前后假设没有变化；
- 大日志只使用了极少部分内容。

### 成本

- Token；
- 时长；
- 上下文污染；
- 重要信息被淹没；
- 主线任务延迟。

### 根因

- 缺少项目导航知识；
- 没有阶段性总结；
- 搜索工具使用不当；
- 任务范围不清；
- 长 Session 遗忘之前已读信息。

### 推荐机制

```text
项目导航说明
探索型 Skill
搜索策略 Checklist
代码索引工具
子任务隔离
Session 分段
```

### 防误报

- 陌生代码库的初期广泛探索可能合理；
- 读取未直接引用的文件，也可能帮助排除假设；
- 只根据“文件数量多”不能判断低效。

---

## 3.7 E06：失败后假设没有更新

### 定义

同一错误反复出现，但 Agent 的问题模型和下一步策略没有发生实质变化。

### 识别信号

- 相同错误指纹重复；
- 连续 Patch 高度相似；
- 命令输入没有实质变化；
- 失败后没有明确诊断；
- 用户提醒“不要继续这样试”；
- 同一文件被反复微调但结果不变。

### 成本

- 重复失败；
- 无效修改；
- 用户接管；
- 会话轮次和时长增加；
- 对错误根因的误导。

### 推荐机制

```text
失败两次后的强制诊断 Checkpoint
调试 Skill
要求说明“本次尝试为什么会不同”
错误分类器
阶段性假设记录
```

### 防误报

- 相同错误可能来自多个独立位置；
- 某些测试天然需要多次迭代；
- 需比较“假设是否变化”，不能只看错误文本。

---

## 3.8 E07：工具输出未进入下一步决策

### 定义

命令、测试、日志或工具结果中已经存在关键证据，但后续行为没有利用它。

### 识别信号

- 非零退出码后没有诊断；
- 日志明确指出权限问题，却修改业务代码；
- 测试失败内容没有在后续消息中被引用；
- 后续动作与工具结果缺少因果关系；
- 同一命令被直接重复。

### 成本

- 误诊；
- 重复执行；
- 无关修改；
- 问题空间扩大；
- 用户重新解释错误。

### 推荐机制

```text
失败工具调用必须生成 Follow-up Diagnosis
工具结果摘要器
调试 Skill
错误分类与路由
```

---

## 3.9 E08：任务范围漂移或过度实现

### 定义

会话实际执行范围逐渐超出初始任务目标，或将修复、重构、优化和文档等多个目标混在一起。

### 识别信号

- 新目标没有用户确认；
- 修改文件超出最初模块；
- “顺便优化”“顺手重构”频繁出现；
- 任务类型多次切换；
- 主任务尚未验证就开始附加工作。

### 成本

- 大 Diff；
- Review 成本；
- 回归风险；
- 测试范围扩大；
- 主任务完成延迟。

### 推荐机制

```text
任务拆分
范围确认 Checkpoint
最小修改规则
Plan 模式
独立 Session
```

### 防误报

- 为完成目标必须修改多个模块不属于漂移；
- 用户显式同意扩展范围则不应判为问题；
- 需要判断新增工作是否推动主目标。

---

## 3.10 E09：用户干预过晚或过密

### 子类型 A：干预过晚

```text
错误方向产生大量修改后才纠正
关键业务约束在实现后才说明
明显风险出现后仍继续执行
```

### 子类型 B：干预过密

```text
用户逐条指定每个命令
AI 每一步都等待下一条指示
没有机会完成一个完整计划—执行—验证循环
```

### 可观察成本

- 被废弃的工具调用；
- 被撤销修改；
- 用户消息密度；
- 决策等待；
- 高价值纠正发生前的无效工作；
- Agent 自主执行碎片化。

### 推荐机制

```text
高风险节点 Checkpoint
低风险步骤批量执行
计划确认后连续运行
关键约束前置
明确允许自主执行的范围
```

### 防误报

- 安全敏感任务需要密集确认；
- 用户教学或演示场景可能有意逐步指导；
- 不应假定干预越少越好。

---

## 3.11 E10：会话过长、任务混杂或交接成本高

### 定义

一个 Session 承载多个目标，或 Session 之间缺少稳定交接，导致上下文污染、重复解释和遗忘。

### 识别信号

- 用户多次切换目标；
- “回到刚才的问题”；
- 后段重复读取早期文件；
- 新 Session 重新介绍相同项目状态；
- 压缩后方向漂移；
- 临时状态和长期项目事实混在一起。

### 成本

- 上下文污染；
- 遗忘；
- 交接轮次；
- 重复探索；
- 旧假设干扰新任务。

### 推荐机制

```text
拆分 Session
Handoff Summary
任务进度文档
AGENTS.md 保存长期事实
PreCompact Summary
```

---

## 3.12 E11：验证债务、假完成或监督成本转移

### 定义

Agent 修改了代码并声明完成，但缺乏测试、构建、Lint、Typecheck、手动验证或结果证据；用户必须在会话后自行补做验证。

### 识别信号

- 存在文件修改但无验证命令；
- 最终回答无命令和结果；
- 测试失败但会话结束；
- 用户重复追问“跑了吗”；
- 用户会话后自行执行验证；
- 验证结果未被 Agent 解释。

### 成本

- 用户二次验证；
- 缺陷延迟发现；
- 重新打开项目；
- 信任下降；
- 返工推迟到后续阶段。

### 推荐机制

| 根因 | 推荐机制 |
|---|---|
| 偶发使用习惯 | Coaching |
| 项目完成标准缺失 | `AGENTS.md` |
| 需要人工提醒 | Checklist |
| 必须自动执行 | Hook |
| 团队级门禁 | CI |
| 命令复杂 | Script |

### 防误报

- 文档或纯研究任务可能不需要测试；
- 某些验证无法在当前环境执行；
- 需区分“不适用”“无法运行”和“遗漏”。

---

## 3.13 E12：权限、环境和外部系统摩擦

### 定义

会话反复受到 Sandbox、权限、依赖、构建环境、CI、Issue、部署平台或外部日志获取的阻碍。

### 识别信号

- `Permission denied` 重复；
- 相同授权多次请求；
- 环境变量反复配置；
- 用户频繁复制 CI、Issue、日志内容；
- 每次都询问当前分支、部署状态或外部系统信息；
- 网络或依赖问题占据大量轮次。

### 成本

- 等待；
- 复制粘贴；
- 重复授权；
- 环境初始化；
- 上下文切换；
- 外部信息过时。

### 推荐机制

```text
环境初始化 Script
安全权限白名单
MCP / API 集成
CI / Issue / 监控连接器
只读工具权限
本地缓存
```

---

# 第四章：解决机制与沉淀路由

## 4.1 固定解决机制枚举

```python
ImprovementMechanism = Literal[
    "coaching",
    "global_instruction",
    "agents_md",
    "path_rule",
    "project_doc",
    "task_template",
    "checklist",
    "script",
    "hook",
    "ci",
    "skill",
    "mcp_integration",
    "environment_config",
    "none",
]
```

## 4.2 路由判断顺序

建议按以下问题依次判断：

```text
1. 这是一次性情况，还是稳定重复？
2. 它是事实、约束、流程、命令还是强制检查？
3. 它需要语言推理还是可以确定性执行？
4. 作用范围是个人、项目、目录、任务类型还是团队？
5. 它必须自动执行吗？
6. 是否需要外部动态数据？
7. 是否值得承担长期维护成本？
```

## 4.3 沉淀路由矩阵

| 内容性质 | 首选载体 | 说明 |
|---|---|---|
| 一次性使用改进 | Coaching | 不污染长期上下文 |
| 跨项目个人偏好 | Global Instruction | 用户级偏好 |
| 稳定项目事实和约束 | `AGENTS.md` | 项目初始化时自动读取 |
| 目录局部规则 | Path Rule | 避免全局污染 |
| 背景知识和架构说明 | Project Doc | 适合人和 Agent 共同阅读 |
| 固定输出结构 | Task Template | 如 Bugfix / Review 模板 |
| 经常漏做的步骤 | Checklist | 人和 Agent 都可执行 |
| 固定命令组合 | Script | 确定性、低维护 |
| 生命周期自动动作 | Hook | 格式化、扫描、结束验证 |
| 团队质量门禁 | CI | 不依赖 Agent 记忆 |
| 多步骤、条件化、可复用流程 | Skill | 有触发场景和专业判断 |
| 外部动态信息 | MCP / API | 不应写死在文档里 |
| 权限、Sandbox、依赖初始化 | Environment Config | 环境层修复 |

---

# 第五章：Skill 候选设计

## 5.1 Skill 不是默认答案

以下内容通常不应该成为 Skill：

- “每次运行 Typecheck”；
- 一个简单命令别名；
- 稳定项目路径；
- 单次用户偏好；
- 纯提醒；
- 可以由 CI 确定性执行的检查。

## 5.2 Skill 候选门槛

建议至少满足：

```text
跨两个以上 Session 出现
包含三个以上有意义步骤
有明确触发场景
步骤间存在判断、分支或专业知识
不是简单命令序列
具有复用价值
能说明输入、输出、验证和失败处理
```

## 5.3 Skill 评分

```text
skill_score =
  recurrence
  + workflow_complexity
  + decision_density
  + reuse_scope
  + evidence_strength
  + verified_benefit
  - deterministic_penalty
  - maintenance_cost
  - staleness_risk
```

推荐阈值：

```text
< 0.45    不建议 Skill
0.45–0.7  候选，等待更多证据
> 0.7     生成 Skill 草稿供 Review
```

## 5.4 Skill Candidate Schema

```python
class SkillCandidate(BaseModel):
    title: str
    trigger: str
    problem_solved: str
    observed_sessions: list[str]
    workflow_steps: list[WorkflowStep]
    required_context: list[str]
    scripts: list[ScriptCandidate]
    verification: list[str]
    failure_modes: list[str]
    confidence: float
    maintenance_risk: float
```

---

# 第六章：大型 Session 分析架构

## 6.1 多层数据金字塔

```text
L0 原始 JSONL
L1 标准化事件
L2 Episode
L3 事实、信号与行为序列
L4 固定问题分类结果
L5 证据审计后的 Findings
L6 Artifact Candidates
L7 report.json / report.html
```

## 6.2 流式读取与证据定位

每个原始事件必须记录：

```text
source_file
line_number
byte_start
byte_end
event_id
timestamp
content_hash
```

报告不复制整份日志，只保存证据引用和短片段。

## 6.3 Episode 分段

推荐 Episode 类型：

```text
task_opening
exploration
planning
implementation
failure_loop
user_correction
verification
finalization
topic_shift
handoff
```

硬切分信号：

- 新用户目标；
- 项目 / cwd 改变；
- 明确重新开始；
- 进入验证；
- 最终回答；
- 目标显著改变。

软切分信号：

- 长时间间隔；
- 连续失败；
- 用户纠正；
- 从读文件转为改文件；
- 从改文件转为测试；
- 工具调用簇结束。

## 6.4 分层摘要

### Episode Summary

记录：

- 局部目标；
- 关键动作；
- 假设；
- 结果；
- 错误；
- 用户纠正；
- 证据引用；
- 未解决问题。

### Session Map

记录：

- 最终目标；
- 目标变化；
- 关键阶段；
- 失败循环；
- 重复要求；
- 重复流程；
- 验证状态；
- 候选问题类型。

## 6.5 Evidence Rehydration

当摘要检测到某问题时：

```text
问题候选
→ 取回相关原始消息、命令与结果
→ 组成 Evidence Packet
→ 重新判断问题是否成立
```

例如 `repeated_user_requirement` 的 Evidence Packet：

```text
首次要求
Agent 违反后的行为
第二次纠正
跨 Session 相似要求
```

## 6.6 Evidence Auditor

审计内容：

- Evidence ID 是否存在；
- 证据是否支持结论；
- 发生次数是否真实；
- 成本是否可观察；
- 根因是否过度推断；
- 问题是否重复；
- 解决机制是否与根因匹配；
- 是否泄露敏感信息。

没有证据的 Finding 不进入报告。

---

# 第七章：LLM 架构

## 7.1 LLM 的固定职责

### 任务 A：语义标准化

把用户要求转为规范断言：

```json
{
  "raw": "别用 npm，这个项目必须用 pnpm",
  "subject": "package_manager",
  "normalized_rule": "use_pnpm",
  "scope": "project",
  "kind": "correction"
}
```

### 任务 B：问题类型分类

模型只能从固定枚举选择：

```text
E01–E12
```

### 任务 C：根因与责任层

输出：

```text
root_cause
responsibility_layers
alternative_explanations
```

### 任务 D：解决机制路由

模型只能从固定机制枚举中选择。

### 任务 E：报告自然语言生成

将已审计数据转成克制、具体、非指责式语言。

## 7.2 LLM 不负责的内容

- 数命令；
- 判断 Exit Code；
- 判断是否运行 Test / Build / Lint；
- 文件大小；
- 重复文件读取次数；
- 命令序列挖掘；
- 时间差计算；
- Evidence ID 有效性；
- 自动修改项目文件。

## 7.3 推荐 Prompt 合约

```text
你是 recodex 的效率问题分类器。

你只能从以下问题类型中选择：...
你只能从以下责任层中选择：...
你只能从以下解决机制中选择：...

要求：
1. 每个问题必须有 evidence_refs。
2. 证据不足时不要输出。
3. 不要输出泛泛最佳实践。
4. 区分必要探索和可避免探索。
5. 不要把所有问题归因给用户。
6. 默认最多返回 5 个候选。
7. 输出必须符合 JSON Schema。
```

## 7.4 模型路由

```text
Fast Model
  用户要求标准化
  Episode 摘要
  初步问题分类

Strong Model
  根因分析
  干预时机判断
  Finding 合并
  解决机制路由
  Evidence 审计辅助

Local Model
  隐私模式摘要
  脱敏后初筛
```

## 7.5 Quick / Deep 模式

### Quick

```text
最新 Session
确定性检测优先
最多 2 次 LLM 调用
只输出 Top 3
10–30 秒目标
```

### Deep

```text
完整 Episode 分析
多分析器并行
跨 Session 聚合
Evidence Rehydration
Evidence Auditor
Artifact Candidate
可断点恢复
```

---

# 第八章：跨 Session 识别

## 8.1 重复用户要求

流程：

```text
抽取用户断言
→ 规范化 Subject / Rule / Scope
→ Embedding + 规则聚类
→ 判断语义一致性
→ 统计 Session 数
→ 判断稳定性
```

稳定性建议：

```text
1 次               一次性事实
同 Session 2 次     候选
跨 2 个 Session     持久化候选
跨 3+ Session       高置信度长期问题
```

## 8.2 重复命令序列

流程：

```text
原始命令
→ 语法解析
→ 参数归一化
→ 动作 Token
→ 序列挖掘
→ 支持度 / 置信度
```

示例：

```text
BUILD → BACKUP → DEPLOY → RESTART → LOGS → HEALTH_CHECK
```

## 8.3 重复流程

命令序列之外，还要结合：

- 用户指令；
- Agent 计划；
- 分支判断；
- 验证方式；
- 失败处理。

两个流程只有在以下内容相似时才可聚类：

```text
触发条件
目标
关键步骤
判断节点
验证结果
```

---

# 第九章：统一数据模型

## 9.1 EfficiencyFinding

```python
class EfficiencyFinding(BaseModel):
    id: str
    problem_type: EfficiencyProblemType
    subtype: str | None = None

    scope: Literal[
        "within_session",
        "cross_session",
        "project",
        "global",
    ]

    title: str
    observation: str
    evidence_refs: list[str]

    occurrences: int
    affected_sessions: list[str]

    observed_cost: "ObservedCost"

    root_cause: str
    alternative_explanations: list[str]

    responsibility_layers: list[ResponsibilityLayer]

    recommendation: str
    mechanism: ImprovementMechanism

    confidence: float
    promotion_confidence: float
```

## 9.2 ObservedCost

```python
class ObservedCost(BaseModel):
    extra_turns: int | None = None
    repeated_commands: int | None = None
    failed_commands: int | None = None
    discarded_changes: int | None = None
    repeated_file_reads: int | None = None
    user_corrections: int | None = None
    tool_output_bytes: int | None = None
    validation_shifted_to_user: bool = False
    wall_time_seconds: int | None = None
    cost_notes: list[str] = []
```

只有可证明时才填写数值。不要生成伪精确百分比。

## 9.3 ArtifactCandidate

```python
class ArtifactCandidate(BaseModel):
    id: str
    source_finding_ids: list[str]
    mechanism: ImprovementMechanism
    target_path: str | None
    title: str
    rationale: str
    proposed_content: str | None
    recurrence: int
    expected_benefit: str
    risks: list[str]
    confidence: float
    status: Literal[
        "proposed",
        "accepted",
        "rejected",
        "applied",
        "deprecated",
    ]
```

---

# 第十章：排序与优先级

## 10.1 Finding 排序

```text
finding_priority =
  observed_cost
  × recurrence
  × evidence_strength
  × actionability
  × expected_benefit
  × confidence
  - false_positive_risk
  - implementation_cost
```

优先展示：

- 成本高；
- 重复发生；
- 有明确证据；
- 能通过一个小动作改善；
- 改进机制清晰。

## 10.2 Artifact 排序

```text
artifact_priority =
  recurrence
  × benefit
  × determinism
  × scope
  × confidence
  - maintenance_cost
  - staleness_risk
  - context_bloat_risk
```

---

# 第十一章：报告设计

## 11.1 首屏必须回答

```text
本次最大可避免成本是什么？
它造成了什么？
最可能的根因是什么？
首要改进动作是什么？
建议沉淀到哪里？
```

示例：

```text
本次最大可避免成本

正确测试入口发现过晚，造成：
- 3 次失败命令
- 2 次错误目录探索
- 1 次用户纠偏

主要成因
测试入口属于稳定项目知识，但没有进入 Codex 默认上下文。

首要改进
将 workspace 范围和标准测试命令加入 AGENTS.md。
```

## 11.2 报告结构

```text
1. 总体判断
2. 最大可避免成本
3. Top 3 效率问题
4. 成本账本
5. 会话流程轨迹
6. 重复要求 / 重复知识 / 重复流程
7. 首要改进动作
8. 沉淀候选
9. 值得保留的做法
10. 证据附录
```

## 11.3 Finding 展示模板

```text
标题

发生了什么
本次会话的具体证据。

可观察成本
轮次、失败命令、重复读取、纠正或返工。

为什么发生
根因和责任层。

建议动作
具体且可执行。

建议沉淀
AGENTS.md / Skill / Script / Hook / CI / 不沉淀。
```

## 11.4 用户不可见内容

默认不展示：

- E01–E12 编号；
- 内部规则编号；
- Raw LLM Prompt；
- 不可靠的伪精确评分；
- 未通过 Evidence Auditor 的候选；
- 低价值的全部问题列表。

---

# 第十二章：Eval 与质量保障

## 12.1 Golden Dataset

至少准备以下案例：

```text
重复用户要求
重复项目知识探索
重复部署流程
重复命令序列
相同失败循环
工具输出被忽略
任务范围漂移
干预过晚
干预过密
长会话上下文污染
无验证完成
权限环境摩擦
无问题的高质量会话
```

每个案例标注：

- 期望问题类型；
- 不应出现的问题类型；
- Evidence；
- 期望责任层；
- 合理解决机制；
- 是否应生成 Skill。

## 12.2 指标

```text
Problem Type Precision
Problem Type Recall
Evidence Accuracy
Root Cause Quality
Mechanism Routing Accuracy
Skill Promotion Precision
Recommendation Actionability
Cross-session Deduplication
False Positive Rate
Report Usefulness
```

最重要的指标不是“模型写得好不好”，而是：

```text
有没有发现真正重复且可改进的问题？
推荐的沉淀方式是否正确？
沉淀后是否减少同类成本？
```

## 12.3 后验效果验证

应用改进后，观察后续 Session：

```text
用户纠正是否减少
首次有效动作是否提前
重复命令是否减少
失败循环是否缩短
验证完成率是否提升
用户监督轮次是否减少
```

Artifact 不应该只记录“已应用”，还要记录：

```text
是否有效
是否过时
是否产生副作用
是否应回滚
```

---

# 第十三章：MVP 范围

## 13.1 第一阶段建议支持 6 类

优先实现：

```text
E01 重复用户要求
E02 稳定项目知识反复发现
E03 固定多步骤流程
E04 固定命令序列
E06 失败后假设未更新
E11 验证债务 / 假完成
```

理由：

- 用户容易感知；
- 证据容易提取；
- 改进机制明确；
- 能直接体现 recodex 的独特价值；
- 适合跨 Session 聚合。

## 13.2 第二阶段

```text
E05 无效探索
E07 工具输出未利用
E08 范围漂移
E09 干预时机
E10 上下文交接
E12 环境集成摩擦
```

## 13.3 MVP 输出

默认报告：

```text
Top 3 Efficiency Findings
Top 1 Primary Action
Top 1–3 Artifact Candidates
Evidence Appendix
```

默认不自动应用任何修改。

---

# 第十四章：实施路线

## Phase 1：数据和确定性信号

- 流式 JSONL Parser；
- 标准事件模型；
- 用户纠正提取；
- 命令标准化；
- 验证命令识别；
- Evidence Ref；
- Episode 分段。

## Phase 2：固定分类 LLM

- E01–E12 Schema；
- 用户要求标准化；
- 固定分类 Prompt；
- Root Cause / Responsibility Layer；
- Structured Output；
- Evidence Auditor。

## Phase 3：跨 Session 聚合

- 断言语义聚类；
- 命令序列挖掘；
- 工作流聚类；
- Recurrence；
- Project-level Findings。

## Phase 4：沉淀机制路由

- `AGENTS.md` Candidate；
- Checklist Candidate；
- Script Candidate；
- Skill Candidate；
- Hook / CI Candidate；
- 人工 Review Queue。

## Phase 5：效果闭环

- Applied Artifact Tracking；
- 后续 Session 对比；
- Benefit Evaluation；
- Deprecated / Rollback；
- 规则和模型迭代。

---

# 第十五章：完整示例

## 15.1 原始现象

最近三个 Session 中：

```text
用户都要求：
“Auth 改动要运行 pnpm test:auth，不要跑根目录全量测试。”
```

同时观察到：

```text
4 次错误测试命令
3 次用户纠正
2 次 workspace 切换
```

## 15.2 固定分类

```json
{
  "problem_type": "repeated_user_requirement",
  "scope": "project",
  "occurrences": 3,
  "responsibility_layers": ["project", "harness"]
}
```

并关联：

```text
project_knowledge_rediscovery
```

## 15.3 根因

```text
Auth 测试入口属于稳定项目知识，
但没有进入 Codex 默认项目上下文。
```

## 15.4 解决机制

```text
首选：AGENTS.md
备选：package.json script / task runner
不推荐：Skill
```

## 15.5 Artifact Candidate

```md
## Auth verification

- For authentication-only changes, run `pnpm test:auth`.
- Do not use the root full-suite command unless the change affects shared packages.
- Run `pnpm typecheck` before marking the task complete.
```

## 15.6 效果验证

未来五个 Auth Session 中观察：

```text
错误测试命令次数
用户纠正次数
首次有效验证出现轮次
任务总轮次
```

如果没有改善：

```text
检查 AGENTS.md 是否被读取
检查规则作用域
考虑加入 Task Runner 或 Hook
```

---

# 第十六章：关键产品结论

1. **固定问题类型，避免 LLM 自由点评。**
2. **问题分类和解决机制都必须是有限枚举。**
3. **每个 Finding 必须有可观察成本和原始证据。**
4. **先判断根因，再决定沉淀载体。**
5. **规则经验库用于解释和路由，不是分析起点。**
6. **不是所有重复问题都应该成为 Skill。**
7. **跨 Session 的重复性，是长期沉淀最重要的证据。**
8. **报告应该突出最高杠杆改进，而不是列出最多的问题。**
9. **Artifact 必须经过人工 Review，并观察后续效果。**
10. **recodex 的核心壁垒是：固定分类体系 + 证据链 + 解决机制路由 + 效果闭环。**

最终产品链路：

```text
行为与事实
→ 固定问题类型
→ 可观察成本
→ 根因和责任层
→ 解决机制
→ 沉淀候选
→ 人工 Review
→ 后续效果验证
```

这套设计能够让 recodex 从“会话总结工具”升级为：

> **一个稳定识别 AI 编程协作低效模式，并把重复问题转化为可执行工程改进的系统。**

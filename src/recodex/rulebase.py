from __future__ import annotations

from dataclasses import dataclass, field

from .analysis import ERROR_TERMS, SANDBOX_TERMS, TEST_TERMS, WORKFLOW_TERMS, count_terms
from .models import SessionRecord, TranscriptEvent
from .transcripts import looks_like_user_correction


@dataclass(frozen=True)
class SourceRef:
    kind: str
    name: str


@dataclass(frozen=True)
class RuleCard:
    id: str
    title: str
    name: str
    category: str
    description: str
    rule_type: str = "diagnostic_and_recommendation"
    suggestions: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()
    confidence: str = "medium"
    maturity: str = "stable"
    scope: tuple[str, ...] = ("personal", "project", "team")


@dataclass(frozen=True)
class RuleResult:
    rule: RuleCard
    applicable: bool
    status: str
    severity: str
    confidence: float
    evidence: tuple[str, ...] = ()
    diagnosis: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = field(default_factory=tuple)


def _rule(
    rule_id: str,
    title: str,
    name: str,
    category: str,
    description: str,
    suggestions: tuple[str, ...],
) -> RuleCard:
    return RuleCard(
        id=rule_id,
        title=title,
        name=name,
        category=category,
        description=description,
        suggestions=suggestions,
        source_refs=(
            SourceRef("official_guidance", "AI coding workflow guidance"),
            SourceRef("community_practice", "agent retrospective practice"),
            SourceRef("project_history", "local Codex session patterns"),
        ),
    )


DEFAULT_RULES: tuple[RuleCard, ...] = (
    _rule("R001", "Prompt should include Goal / Context / Constraints / Done when", "Prompt 应包含目标、上下文、约束和完成条件", "prompt_quality", "任务开始时应给出目标、必要上下文、约束和完成标准。", ("把复杂任务 prompt 改成 Goal / Context / Constraints / Done when 四段式。",)),
    _rule("R002", "Bugfix should reproduce before patch", "Bugfix 应先复现再修改", "bugfix_workflow", "Bug 修复应先理解或复现问题，再做最小修复并验证。", ("在 bugfix prompt 中加入：先复现问题，不要在解释失败路径前修改代码。", "Bugfix 完成前必须包含：复现、修复、最小相关测试、结果报告。")),
    _rule("R003", "Bugfix should keep patch minimal", "Bugfix 应保持最小修复", "bugfix_workflow", "修 bug 时应避免顺手重构或扩大修改范围。", ("把最小修复原则写入 bugfix checklist。",)),
    _rule("R004", "Bugfix should add regression test when practical", "Bugfix 应尽量添加回归测试", "bugfix_workflow", "可复现的 bug 应尽量用测试锁住回归。", ("为可复现问题增加回归测试或说明为什么暂时不能加。",)),
    _rule("R005", "Final response should report verification commands and results", "最终回复应报告验证命令和结果", "verification", "完成前应运行相关验证，并在最终回复中说明命令和结果。", ("在 AGENTS.md 加入：没有验证命令不得声明完成。", "生成 completion checklist。")),
    _rule("R006", "Complex tasks should explore before planning", "复杂任务应先探索再计划", "task_planning", "复杂任务应先读取相关上下文，再制定计划。", ("复杂任务默认执行 Explore → Plan → Act。",)),
    _rule("R007", "Multi-file changes should have a plan", "多文件修改前应先给计划", "task_planning", "多文件修改需要先说明步骤和影响面。", ("多文件任务先列计划和验证点。",)),
    _rule("R008", "Simple tasks should avoid over-planning", "简单任务不应过度规划", "task_planning", "小修小改不应被复杂流程拖慢。", ("简单任务直接执行，避免无意义计划。",)),
    _rule("R009", "Refactors should be staged and reversible", "重构任务应分阶段并可回滚", "task_planning", "重构应拆成小步，保持可验证和可回滚。", ("重构任务用分阶段 checklist。",)),
    _rule("R010", "UI tasks need visual verification", "UI 任务应有视觉验证", "verification", "UI 改动应做截图、浏览器或视觉检查。", ("UI 任务完成前运行视觉验证。",)),
    _rule("R011", "Deploy tasks should check status / logs / health", "部署任务应检查状态、日志和健康接口", "verification", "部署后必须验证服务状态、日志和健康检查。", ("部署流程固化为 Build → Restart → Logs → Health Check。",)),
    _rule("R012", "Review tasks should focus risk", "Review 任务应关注边界条件、安全和回归风险", "collaboration", "代码 review 应优先关注 bug、风险、回归和测试缺口。", ("review 输出按严重度列问题。",)),
    _rule("R013", "Failed commands must inspect error output", "命令失败后必须读取错误输出", "tool_usage", "命令失败后应基于 stderr/stdout 继续诊断，而不是盲试。", ("失败命令后先总结错误，再决定下一步。",)),
    _rule("R014", "Repeated failures indicate wrong assumptions", "重复失败说明假设可能错误", "tool_usage", "同类失败重复出现时应暂停并重新检查假设。", ("连续失败后强制写出当前假设和替代路径。",)),
    _rule("R015", "Repeated user corrections should become rules", "用户两次纠正同类问题应沉淀规则", "user_correction", "用户重复纠正说明项目事实或工作流需要持久化。", ("把重复纠正写入 AGENTS.md 或 checklist。",)),
    _rule("R016", "Long sessions should clear or compact", "长会话应 clear / compact / 分 session", "context_management", "长会话上下文过大时应切分或压缩。", ("长任务按阶段产出 summary 并开启新 session。",)),
    _rule("R017", "Large logs should be chunked", "大日志应切片读取，不应全文塞入上下文", "context_management", "大日志和大 transcript 应按 chunk 读取和摘要。", ("大日志只取关键段、首尾和错误附近上下文。",)),
    _rule("R018", "Repeated project facts should go to project rules", "重复项目事实应写入 AGENTS.md / CLAUDE.md / rules", "project_memory", "重复出现的项目路径、命令和约束应沉淀为项目规则。", ("把项目测试命令、目录结构和完成定义写入 AGENTS.md。",)),
    _rule("R019", "Multi-step workflows should become skills or checklists", "多步流程应升级为 skill 或 checklist", "automation", "多步可复用流程应从聊天过程升级为 skill 或 checklist。", ("把高频流程沉淀为 skill/checklist。",)),
    _rule("R020", "Repeated command sequences should become scripts", "重复命令序列应脚本化", "automation", "重复执行的命令序列应变成脚本、Make target 或 npm script。", ("把重复验证流程写入 scripts/ai。",)),
    _rule("R021", "Repeated verification failures should become CI or eval", "重复验证失败应转成 CI / eval", "automation", "重复验证失败说明需要自动化门禁。", ("把重复 typecheck/test/eval 失败转成 CI。",)),
    _rule("R022", "Auth/payment/security changes need stronger review", "修改 auth / payment / security 需要更强 review", "safety", "高风险领域改动需要更严格验证和 review。", ("安全敏感改动强制 security checklist。",)),
    _rule("R023", "New dependencies need risk review", "新增依赖需要安全和维护风险检查", "safety", "新增依赖应检查安全、许可证和维护风险。", ("新增依赖前说明必要性和风险。",)),
    _rule("R024", "Secrets and env files need redaction", "secrets / .env 暴露需要脱敏和 guardrail", "safety", "会话中出现密钥、token、.env 或敏感路径时必须脱敏。", ("开启默认脱敏并避免把 secret 写入输出物。",)),
    _rule("R025", "Productivity should be measured by rework and verification", "不要用主观感觉判断提效", "productivity_metrics", "AI 提效要看返工、验证和重复问题下降。", ("用复盘指标衡量返工和验证情况。",)),
    _rule("R026", "Final answer needs evidence", "最终答案不能只说完成，要说明证据", "verification", "最终回复应包含变更摘要、验证命令和剩余风险。", ("最终回复模板加入 evidence / commands / risk。",)),
    _rule("R027", "Missing project test commands cause repeated exploration", "项目测试命令缺失会造成重复探索", "project_memory", "如果每次都重新找测试命令，应写入项目规则。", ("把项目 test/build/typecheck 命令写入 AGENTS.md。",)),
    _rule("R028", "Scope drift should trigger goal reconfirmation", "任务范围漂移时应重新确认目标", "task_planning", "任务方向变化或范围扩大时应重新确认目标。", ("范围漂移时输出当前目标和待确认点。",)),
    _rule("R029", "Too many tool calls without progress should summarize assumptions", "工具调用过多但无进展时应总结当前假设", "tool_usage", "大量工具调用但问题未收敛时，应暂停总结假设。", ("高命令量 session 中途生成假设和下一步。",)),
    _rule("R030", "High-risk commands need confirmation", "高风险命令应需要用户确认或 hook 拦截", "safety", "破坏性、生产、权限提升命令应先确认。", ("为高风险命令增加人工确认或 hook。",)),
)

EXTENDED_RULES: tuple[RuleCard, ...] = (
    _rule("R031", "Tasks need acceptance criteria", "任务没有验收标准时，不能判断完成", "prompt_quality", "任务缺少成功表现、测试方式或不允许改动范围时，AI 容易自行定义完成。", ("下次 prompt 自动补充 Done when：测试通过、bug 不再复现、UI 与截图一致或 API 行为不变。",)),
    _rule("R032", "Ambiguous tasks should ask clarifying questions", "需求含糊时，应先反问而不是直接执行", "prompt_quality", "目标含糊且可能有多个方向时，应先澄清关键约束。", ("当任务歧义高时，先提出最多 3 个澄清问题，再编辑文件。",)),
    _rule("R033", "High-risk prompts need explicit non-goals", "高风险任务必须显式声明不做什么", "prompt_quality", "涉及安全、权限、支付、迁移或生产环境时，prompt 应包含明确约束。", ("高风险任务补充 Constraints：不改 API shape、不改 schema、不碰生产数据、不重写大模块。",)),
    _rule("R034", "Complex work should start with task breakdown", "复杂任务应先输出任务分解", "task_planning", "多阶段、多服务或前后端联动任务应先形成理解、假设、计划和验证方案。", ("复杂任务先输出 understanding、assumptions、implementation plan、likely files、verification plan。",)),
    _rule("R035", "Small tasks should stay lightweight", "小任务不要强行套复杂流程", "task_planning", "简单改文案、typo、常量或配置不应被过度流程拖慢。", ("小任务走 quick edit + minimal verification，不进入 full planning。",)),
    _rule("R036", "Multi-file edits need impact map", "多文件修改前应建立影响面地图", "task_planning", "多文件修改前应说明入口、调用链、受影响模块和测试目标。", ("多文件任务先列 entry points、affected modules、callers/callees、test targets、risky boundaries。",)),
    _rule("R037", "Chaotic file reading indicates weak exploration", "读文件顺序混乱说明探索策略差", "context_management", "反复打开无关文件或重复 grep 同一概念，说明定位路径不清。", ("探索规则：先找入口，再找调用链，再找测试，最后读实现细节。",)),
    _rule("R038", "Plans need verification steps", "计划如果没有验证步骤，就是不完整计划", "task_planning", "计划只有实现步骤而没有 test/build/lint/manual verification 时，不足以判断完成。", ("计划模板强制包含 Verification Plan。",)),
    _rule("R039", "Risky refactors need rollback plan", "高风险重构需要 rollback / fallback", "task_planning", "重构、迁移和部署任务应包含分阶段执行、回滚和兼容性检查。", ("重构/迁移/部署任务增加 staged rollout、rollback plan、compatibility check。",)),
    _rule("R040", "Work should confirm cwd and branch", "AI 开始执行前应该确认当前目录和分支", "tool_usage", "因 cwd、repo 或 branch 错误导致命令失败，会造成低级返工。", ("任务开始自动检查 pwd、git status --short、git branch --show-current。",)),
    _rule("R041", "Bugfix needs failure evidence", "Bugfix 如果没有失败证据，容易变成猜测式修复", "bugfix_workflow", "没有错误日志、失败测试、复现步骤或具体失败行为时，不应直接改代码。", ("Bugfix 先收集明确错误、关键栈帧、失败命令、期望行为和实际行为。",)),
    _rule("R042", "Bugfix needs before/after proof", "修复前后没有对比，不能证明 bug 已解决", "bugfix_workflow", "改前失败状态和改后通过状态都应留下证据。", ("记录修复前的失败行为和修复后的通过行为。",)),
    _rule("R043", "Repeated identical errors require hypothesis reset", "同一错误连续出现 2 次，说明当前假设可能错了", "debugging", "同一错误连续出现时，继续小修小补通常会扩大试错。", ("连续同错后暂停 patch，总结当前假设，解释为什么失败仍存在，再提出新假设。",)),
    _rule("R044", "Avoid symptom-only patches", "修复只处理 symptom，不处理 root cause", "debugging", "用 try/catch、默认值、跳过测试或放宽类型掩盖错误时，需要根因证明。", ("修复说明必须包含 root cause、why this fix is correct、regression proof。",)),
    _rule("R045", "Debugging should cite key log lines", "用户提供错误日志后，AI 应引用日志关键行", "debugging", "用户贴出日志后，诊断应指出关键错误行、栈帧或命令输出。", ("Debugging responses must identify the exact failing line / stack frame / command output。",)),
    _rule("R046", "Bugfix scope should not spread", "AI 修改了不相关代码，说明 bug scope 扩散", "bugfix_workflow", "修 bug 时顺手改 formatting、架构或 UI，会增加回归风险。", ("Bugfix should be minimal unless user explicitly asks for refactor。",)),
    _rule("R047", "Code changes require verification", "代码修改后没有任何验证命令，默认高风险", "verification", "存在代码改动但没有 test/build/lint/typecheck/dev server/screenshot/health check 时，完成信心不足。", ("给项目增加完成检查清单；若重复出现，升级为 Stop hook 或 CI 门禁。",)),
    _rule("R048", "Do not claim completion after failed verification", "验证失败后仍然宣布完成，是严重违规", "verification", "验证命令失败或输出含错误时，最终答案不能声明完成。", ("最终回复必须列出成功命令、失败命令、未完成风险和下一步。",)),
    _rule("R049", "Prefer smallest relevant test in debug loop", "只跑全量测试但没有最小相关测试，调试效率可能低", "verification", "每次只跑全量测试会放大等待和日志噪音。", ("先找 smallest relevant test：single file、single case、package-level test。",)),
    _rule("R050", "Final confidence needs integration gate", "只跑最小测试但不跑最终集成验证，完成信心不足", "verification", "跨模块任务只跑最小单测，不足以证明整体行为。", ("两阶段验证：inner loop 跑最小相关测试，final gate 跑项目标准检查。",)),
    _rule("R051", "UI work needs visual proof", "UI 任务没有视觉验证，容易代码对但画面错", "verification", "布局、CSS、组件和截图任务应有浏览器或截图验证。", ("UI verification：run dev server、提供 URL、截图并与目标比较。",)),
    _rule("R052", "Deploy needs logs/status/health", "部署任务没有 logs / status / health check，不能算完成", "verification", "restart/deploy 后必须验证服务状态、日志、健康接口和 smoke test。", ("部署完成标准：服务启动、日志无关键错误、health 正常、smoke test 通过。",)),
    _rule("R053", "Final answer should list verification results", "最终回答没有列出验证结果，用户无法审查", "verification", "最终回答只说完成但没有命令、文件、测试或风险，用户无法复核。", ("最终回答模板：changed files、commands run、results、not run / why、remaining risks。",)),
    _rule("R054", "Mixed-goal long sessions are risky", "长会话中不同任务混在一起，会显著增加误判风险", "context_management", "同一 session 混合 bugfix、refactor、deploy、docs 等目标会增加方向漂移。", ("任务变更时生成 handoff summary 后新开会话。",)),
    _rule("R055", "Large logs should be sliced", "大日志应该切片，而不是全文塞入上下文", "context_management", "完整 build/server/test log 会污染上下文并降低诊断质量。", ("grep error、只保留 first/last、提取 stack trace，超大 stdout 不进 LLM。",)),
    _rule("R056", "Repeated file reads indicate context inefficiency", "重复读取同一文件说明上下文索引不足", "context_management", "同一文件反复读取说明关键发现没有被压缩成可用上下文。", ("增加文件摘要缓存和 session notes，compact 前保留 file facts。",)),
    _rule("R057", "After compaction, return to task summary", "自动 compact 后如果方向漂移，应当回到任务摘要", "context_management", "compact 后忘记目标或重复旧步骤时，需要回到 handoff summary。", ("PreCompact summary 包含 goal、current hypothesis、files changed、failed attempts、remaining verification。",)),
    _rule("R058", "Exploration can be isolated from main context", "探索型阅读适合隔离到子任务", "context_management", "大规模调查或日志阅读只应把蒸馏后的发现带回主会话。", ("把探索、代码审查、大日志分析分离成 read-only 子任务或独立 session。",)),
    _rule("R059", "Repeated project facts should become project rules", "同一项目事实被用户重复解释，应写入项目规则", "project_memory", "测试命令、目录、服务名、部署方式等被重复解释时，应持久化。", ("把重复项目事实写入 AGENTS.md / CLAUDE.md / Cursor rule。",)),
    _rule("R060", "Process corrections should become skills/checklists", "用户纠正的是多步流程时，不应写成全局规则", "project_memory", "多步部署、调试或验证流程应落到 skill/checklist，而不是全局一句话规则。", ("把流程型纠正输出为 skill 或 checklist。",)),
    _rule("R061", "Local facts need path-scoped rules", "用户纠正的是局部路径规则，应使用 path-scoped rule", "project_memory", "只适用于某目录、模块或技术栈的规则不应污染全局。", ("生成 path-scoped rule、module-specific note 或 local checklist。",)),
    _rule("R062", "One-off preferences should not become long-term rules", "用户纠正的是一次性偏好，不应沉淀", "project_memory", "一次性偏好不等于长期约定。", ("标记 one-off preference，不生成长期规则。",)),
    _rule("R063", "Goal rejection indicates missing understanding check", "用户中途说不是这个意思，说明初始理解没有被确认", "prompt_quality", "复杂任务开头没有复述理解，容易出现方向重置。", ("复杂任务开头输出 understanding 和 assumptions。",)),
    _rule("R064", "Repeated manual checks should become automation", "重复出现的人工检查，应转成 hook 或脚本", "automation", "反复靠用户提醒跑 typecheck、查 secrets、看 diff 或格式化，说明需要自动化。", ("候选改进：Stop hook、pre-commit hook、npm script 或 Makefile target。",)),
    _rule("R065", "Hard requirements need enforcement, not just prompts", "规则如果必须强制执行，不能只写进提示词", "automation", "安全、合规或生产风险规则不能只靠 AGENTS.md 提醒。", ("强制规则升级为 hook / permission policy。",)),
    _rule("R066", "PostToolUse cannot block completed actions", "PostToolUse 只能审计，不能阻止已发生的危险动作", "automation", "命令执行后的 hook 无法撤销危险动作。", ("用 PreToolUse 做阻止，PostToolUse 做审计。",)),
    _rule("R067", "Stop hook is not task completion", "Stop hook 不能等同于任务完成 hook", "automation", "Stop 只代表一次响应结束，不代表任务真的完成。", ("结合 final answer、file changes、verification、user acceptance 判断任务完成。",)),
    _rule("R068", "Failed automation should block completion", "自动化检查失败时，AI 应返回未完成", "automation", "hook/test/lint 失败时，AI 不能绕过检查或继续声明完成。", ("Automation failures are blockers unless explicitly waived by the user。",)),
    _rule("R069", "High-risk commands must be flagged", "高风险命令必须被识别和标记", "safety", "rm -rf、sudo、kubectl、terraform、生产部署、数据库迁移等需要特殊处理。", ("高风险命令要求用户确认、dry-run、备份、沙箱执行和回滚方案。",)),
    _rule("R070", "Sensitive config path edits need review", "修改 .git、IDE 配置、agent 配置等敏感路径要单独提醒", "safety", "修改仓库状态、IDE、agent、hook、CI 或 lockfile 会影响后续工具行为。", ("对 agent/config/protected path 改动要求更高 review。",)),
    _rule("R071", "Secrets must be redacted", "读取 .env / secret 文件后必须脱敏", "safety", "读取或输出 .env、API key、token、private key、cookie、database URL 必须脱敏。", ("复盘报告和建议必须脱敏，并增加 secret scan hook。",)),
    _rule("R072", "New dependencies need necessity/risk explanation", "新增依赖必须解释必要性和风险", "safety", "新增依赖会带来安全、维护、bundle 或运行时影响。", ("说明为什么不能用现有依赖、维护状态、安全风险、bundle/runtime impact。",)),
    _rule("R073", "Auth/payment/crypto changes need stronger validation", "认证、权限、支付、加密相关改动必须提高验证级别", "safety", "auth、oauth、jwt、rbac、payment、crypto 等改动应更严格验证。", ("要求 security-focused review、negative tests、edge cases、no silent fallback。",)),
    _rule("R074", "Large generated code needs design rationale", "AI 生成代码如果没有解释设计取舍，review 成本会上升", "collaboration", "大段改动若不解释关键设计，会增加 review 成本。", ("最终回答包含 key decisions、tradeoffs、alternatives rejected。",)),
    _rule("R075", "PR review should produce findings/checklist", "PR 类任务应该输出 review checklist，而不是只给总结", "collaboration", "PR/diff review 应关注 correctness、regression、安全、性能和测试缺口。", ("PR review 输出：correctness、regression risk、security、performance、tests missing、questions。",)),
    _rule("R076", "Tests should cover edge cases", "AI 只看 happy path，说明测试建议不足", "verification", "新增测试只覆盖正常路径，会漏掉边界和失败路径。", ("测试规则加入 null/empty、permission denied、invalid input、concurrency、network failure。",)),
    _rule("R077", "Refactor needs behavior equivalence proof", "重构没有行为等价证明，风险偏高", "verification", "重构后没有测试、snapshot 或 before/after 行为对比，风险偏高。", ("重构要求 no behavior change statement、test coverage、diff review、regression suite。",)),
    _rule("R078", "Large diffs should be split", "大 diff 应拆分，否则 review 难度过高", "collaboration", "一次改动混合 refactor、feature、formatting、tests 会显著增加 review 难度。", ("拆成 mechanical refactor、behavior change、tests、cleanup。",)),
    _rule("R079", "Do not rerun failing commands unchanged", "反复运行失败命令但不改变输入，说明循环无效", "tool_usage", "同一命令失败多次且输入无变化时，循环没有新信息。", ("再次运行前解释为什么这次会不同。",)),
    _rule("R080", "Failed tool output must be used", "命令输出没有被使用，是 tool result waste", "tool_usage", "工具输出已有关键错误但后续没有引用或处理，会浪费上下文和时间。", ("Every failed command must produce a follow-up diagnosis。",)),
    _rule("R081", "Search should be scoped", "全仓库 grep 太多，说明缺少索引策略", "tool_usage", "大量无范围 grep/find 会产生噪音。", ("搜索策略：限定目录、搜 exact symbol、先找 tests、再找 callers。",)),
    _rule("R082", "Prefer existing project scripts", "AI 不使用项目已有脚本，说明 project commands 缺失", "tool_usage", "项目已有脚本时，AI 不应手写复杂命令。", ("项目规则写入：Prefer existing project scripts before inventing commands。",)),
    _rule("R083", "Wrong cwd is a workflow basics failure", "命令在错误目录运行，应标记 workflow 基础错误", "tool_usage", "No such file、package.json not found、module not found 常来自 cwd 错误。", ("下次任务开始先确认 cwd / repo root / package workspace。",)),
    _rule("R084", "Rules should stay short", "规则太长会降低遵循率", "project_memory", "过长的 AGENTS.md / CLAUDE.md / rules 会降低稳定遵循率。", ("压缩为 always rules、project facts、commands、done definition，复杂流程移到 skill/checklist。",)),
    _rule("R085", "Rules need scope", "规则没有适用范围，会被错误触发", "project_memory", "规则如果没有 task/path/language/risk scope，会被误用。", ("给规则加 task_type、path、language、framework、risk level。",)),
    _rule("R086", "Rules need expiry conditions", "规则没有失效条件，容易变成技术债", "project_memory", "旧目录、旧命令、旧框架规则会污染后续会话。", ("规则卡增加 last_verified_at、valid_for_paths、expires_if、source evidence。",)),
    _rule("R087", "Repeated violations need enforcement escalation", "同一规则被多次违反，说明它不该只是提醒", "automation", "反复违反没跑测试、忘记日志、改错目录或暴露 secret，说明提醒不够。", ("升级路径：提醒 → checklist → hook → CI/eval → 权限策略。",)),
    _rule("R088", "Smooth-looking sessions are not proof of productivity", "不能用会话看起来顺利判断提效", "productivity_metrics", "快速答复但缺验证、后续返工或 bug 复现，不能算真正提效。", ("复盘看 correction count、failed command count、rework count、verification present、time to accepted result。",)),
    _rule("R089", "Subjective speed is not actual speed", "主观觉得 AI 更快，不代表实际更快", "productivity_metrics", "主观评价需要和失败循环、返工、重复纠正对照。", ("引入 wall-clock duration、turns、failed commands、user corrections、accepted patch count。",)),
    _rule("R090", "Downstream cost matters", "如果 AI 把成本转移到 review / QA / ops，不能算真正提效", "productivity_metrics", "PR review、CI、QA、deploy 中暴露的问题也是 AI 开发成本。", ("复盘增加 downstream cost：review defects、CI retries、bug reopen、rollback、incident。",)),
    _rule("R091", "Repeated failure patterns should become evals", "重复失败模式应转成 eval，而不是只写总结", "automation", "同类错误跨 session 反复出现时，应形成可运行检查。", ("生成 prompt regression test、static check、CI rule 或 scripted harness。",)),
    _rule("R092", "Retros should recommend minimal verifiable improvements", "会话复盘应输出最小可验证改进", "automation", "复盘建议过多且无优先级时，不容易落地。", ("每次只推荐 Top 1-3 改进，包含 impact、effort、evidence、success metric。",)),
    _rule("R093", "Recommendations need evidence", "改进建议必须绑定证据，否则容易成为泛泛最佳实践", "automation", "建议没有用户纠正、失败命令、未验证或重复问题证据时，容易泛化。", ("建议格式：Observation → Rulebase basis → Evidence → Action。",)),
    _rule("R094", "Advice should map to artifacts", "如果建议无法落地到载体，说明建议不够具体", "automation", "建议若不能映射到 AGENTS.md、checklist、skill、script、hook、CI 等载体，就还不够具体。", ("改进建议必须能映射到 AGENTS.md、checklist、skill、script、hook、CI、prompt template 或 review rule。",)),
    _rule("R095", "Cross-tool work needs handoff summary", "不同工具接力时，必须生成 handoff summary", "collaboration", "Cursor、Claude Code、Codex 等工具接力时，需要明确当前状态。", ("handoff summary 包含 goal、status、files changed、decisions、failed attempts、next step、verification remaining。",)),
    _rule("R096", "Tool choice should match task shape", "适合 IDE 的任务和适合 CLI agent 的任务应区分", "collaboration", "局部 UI 和大型 repo 调查适合不同工具。", ("工具选择：局部 UI 用 IDE，多文件命令/测试/重构用 CLI agent，重复任务用 automation。",)),
    _rule("R097", "Use read-only agents for broad exploration", "大量探索任务可以交给只读 agent，主 agent 保持干净", "collaboration", "主会话塞入大量探索结果会污染实现上下文。", ("用 read-only exploration、reviewer subagent、summarizer subagent，主 agent 只接收 distilled findings。",)),
    _rule("R098", "Conflicting agent outputs need arbitration", "多 agent 输出冲突时，应显式做仲裁", "collaboration", "多个 agent 建议冲突时，必须比较假设和风险。", ("仲裁：compare assumptions、compare risk、choose lower-risk path、preserve alternative。",)),
    _rule("R099", "Personal preferences need validation before team rules", "个人偏好不能直接升级为团队规则", "collaboration", "一次个人偏好不应直接写成团队规范。", ("升级团队规则前要求多次出现、review 支持、团队确认、不冲突。",)),
    _rule("R100", "Team rules need rationale", "团队规则必须有为什么，否则难以维护", "collaboration", "团队规则只有必须/禁止而没有理由，会变成难维护的口号。", ("团队规则卡包含 rationale、examples、applies_when、exceptions。",)),
)

DEFAULT_RULES = DEFAULT_RULES + EXTENDED_RULES

DEFAULT_ENABLED_RULE_IDS = frozenset(
    {f"R{number:03d}" for number in range(1, 31)}
    | {
        "R031",
        "R038",
        "R041",
        "R042",
        "R047",
        "R048",
        "R052",
        "R054",
        "R055",
        "R059",
        "R064",
        "R065",
        "R069",
        "R071",
        "R078",
        "R080",
        "R087",
        "R088",
        "R091",
        "R093",
    }
)


def list_rules() -> tuple[RuleCard, ...]:
    return DEFAULT_RULES


def get_rule(rule_id: str) -> RuleCard | None:
    normalized = rule_id.strip().upper()
    return next((rule for rule in DEFAULT_RULES if rule.id == normalized), None)


def evaluate_session_rules(
    session: SessionRecord,
    events: list[TranscriptEvent],
    *,
    limit: int = 8,
    enabled_ids: set[str] | frozenset[str] | None = None,
) -> list[RuleResult]:
    facts = _facts(session, events)
    active_ids = DEFAULT_ENABLED_RULE_IDS if enabled_ids is None else frozenset(rule_id.upper() for rule_id in enabled_ids)
    active_rules = [rule for rule in DEFAULT_RULES if rule.id in active_ids]
    results = [_evaluate_rule(rule, session, events, facts) for rule in active_rules]
    relevant = [result for result in results if result.applicable and result.status != "satisfied"]
    relevant.sort(key=lambda item: (_severity_rank(item.severity), item.rule.id), reverse=True)
    if len(relevant) < limit:
        satisfied = [result for result in results if result.applicable and result.status == "satisfied"]
        relevant.extend(satisfied[: limit - len(relevant)])
    return relevant[:limit]


def _facts(session: SessionRecord, events: list[TranscriptEvent]) -> dict[str, object]:
    signal_events = [event for event in events if _is_rule_signal_event(event)]
    text = "\n".join(event.text for event in signal_events)
    lowered = text.lower()
    user_events = [event for event in signal_events if event.role == "user"]
    assistant_events = [event for event in signal_events if event.role == "assistant"]
    commands = [event for event in signal_events if event.metadata.get("command") or "command" in event.kind.lower()]
    corrections = [event for event in user_events if looks_like_user_correction(event.text)]
    verification_count = count_terms(text, TEST_TERMS)
    error_count = session.error_count + count_terms(text, ERROR_TERMS)
    sandbox_count = count_terms(text, SANDBOX_TERMS)
    workflow_count = count_terms(text, WORKFLOW_TERMS)
    categories = {
        "bugfix": _has_any(lowered, ("bug", "fix", "error", "failed", "失败", "报错", "修复")),
        "deploy": _has_any(lowered, ("deploy", "restart", "systemctl", "health", "部署", "发布")),
        "ui": _has_any(lowered, ("ui", "frontend", "react", "page", "screen", "视觉", "页面")),
        "review": _has_any(lowered, ("review", "审查", "评审")),
        "refactor": _has_any(lowered, ("refactor", "重构")),
        "docs": _has_any(lowered, ("docs", "readme", "文档")),
        "feature": _has_any(lowered, ("feature", "implement", "新增", "实现")),
    }
    command_text = "\n".join(str(event.metadata.get("command") or event.text) for event in commands).lower()
    last_assistant_text = assistant_events[-1].text.lower() if assistant_events else ""
    return {
        "text": text,
        "lowered": lowered,
        "user_events": user_events,
        "assistant_events": assistant_events,
        "commands": commands,
        "corrections": corrections,
        "verification_count": verification_count,
        "error_count": error_count,
        "sandbox_count": sandbox_count,
        "workflow_count": workflow_count,
        "command_text": command_text,
        "last_assistant_text": last_assistant_text,
        "is_bugfix": categories["bugfix"],
        "is_deploy": categories["deploy"],
        "is_ui": categories["ui"],
        "is_review": categories["review"],
        "is_refactor": categories["refactor"],
        "mixed_task_count": sum(1 for matched in categories.values() if matched),
        "has_plan": _has_any(lowered, ("plan", "计划", "步骤", "implementation plan", "todo", "分解")),
        "has_acceptance_criteria": _has_any(lowered, ("done when", "acceptance", "验收", "完成标准", "成功表现", "expected", "测试通过", "不允许")),
        "has_reproduction": _has_any(lowered, ("repro", "reproduce", "复现", "failing test", "失败证据", "stack trace", "traceback", "错误日志", "expected vs actual")),
        "has_before_after": _has_any(lowered, ("before/after", "before and after", "改前", "改后", "前后对比", "passing behavior", "failing behavior")),
        "has_deploy_checks": _has_any(lowered, ("systemctl status", "journalctl", "docker logs", "health", "smoke test", "状态", "日志", "健康")),
        "has_final_claim": _has_any(last_assistant_text, ("done", "completed", "fixed", "passed", "完成", "已修复", "修好了", "通过")),
        "has_followup_diagnosis": _has_any(lowered, ("diagnos", "root cause", "原因", "因为", "错误输出", "traceback", "stack trace", "假设")),
        "has_automation": _has_any(lowered, ("hook", "pretooluse", "stop hook", "ci", "github actions", "pre-commit", "guardrail", "permission policy", "脚本", "自动化")),
        "has_high_risk_command": _has_any(command_text, ("rm -rf", "sudo", "chmod -r", "curl | sh", "kubectl apply", "kubectl delete", "terraform apply", "docker system prune", "drop table", "migrate", "production", "prod")),
        "has_sensitive_config": _has_any(lowered, (".git", ".vscode", ".idea", ".husky", ".codex", ".claude", ".mcp.json", "package-lock", "pnpm-lock", "github/workflows")),
        "has_secret": _has_any(lowered, ("api_key", "token", "password", ".env", "secret", "authorization")),
        "has_auth_security": _has_any(lowered, ("auth", "payment", "security", "jwt", "权限", "支付", "安全")),
        "has_dependency": _has_any(lowered, ("pip install", "npm install", "uv add", "dependency", "依赖")),
        "has_large_log": any(len(event.text) > 8000 or event.kind == "huge_line" for event in signal_events),
        "large_diff_risk": session.message_count >= 40 or session.command_count >= 20 or _has_any(lowered, ("large diff", "大 diff", "many files", "多个文件", "formatting", "格式化")),
    }


def _evaluate_rule(
    rule: RuleCard,
    session: SessionRecord,
    events: list[TranscriptEvent],
    facts: dict[str, object],
) -> RuleResult:
    commands = facts["commands"]
    verification_count = int(facts["verification_count"])
    error_count = int(facts["error_count"])
    sandbox_count = int(facts["sandbox_count"])
    workflow_count = int(facts["workflow_count"])
    corrections = facts["corrections"]

    if rule.id == "R031":
        status = "satisfied" if facts["has_acceptance_criteria"] else "partial"
        return _result(rule, True, status, "medium", ("acceptance criteria scan",), ("任务缺少验收标准时，AI 容易自行定义完成。",))
    if rule.id == "R038":
        applicable = bool(facts["has_plan"]) or session.message_count >= 8
        status = "satisfied" if applicable and verification_count else "violated"
        return _result(rule, applicable, status, "high", (f"plan signal={facts['has_plan']}, verification signals={verification_count}",), ("计划缺少验证步骤。",))
    if rule.id == "R041":
        applicable = bool(facts["is_bugfix"])
        status = "satisfied" if applicable and facts["has_reproduction"] else "violated"
        return _result(rule, applicable, status, "high", ("bugfix signal detected",), ("Bugfix 缺少失败证据，容易变成猜测式修复。",))
    if rule.id == "R042":
        applicable = bool(facts["is_bugfix"])
        status = "satisfied" if applicable and facts["has_before_after"] else "violated"
        return _result(rule, applicable, status, "high", ("bugfix signal detected",), ("缺少修复前后的可审查证据。",))
    if rule.id == "R047":
        applicable = session.command_count > 0 or _has_any(str(facts["lowered"]), ("edit", "modified", "patched", "implemented", "changed", "修改", "实现", "修复"))
        status = "satisfied" if applicable and verification_count else "violated"
        return _result(rule, applicable, status, "high", (f"commands={session.command_count}, verification signals={verification_count}",), ("代码修改后没有检测到验证命令。",))
    if rule.id == "R048":
        applicable = error_count > 0 and bool(facts["has_final_claim"])
        status = "violated" if applicable else "not_applicable"
        return _result(rule, applicable, status, "high", (f"error signals={error_count}, final claim={facts['has_final_claim']}",), ("验证失败后不能宣布完成。",))
    if rule.id == "R052":
        applicable = bool(facts["is_deploy"])
        status = "satisfied" if applicable and facts["has_deploy_checks"] else "violated"
        return _result(rule, applicable, status, "high", ("deploy-like task detected",), ("部署任务缺少 logs/status/health/smoke test。",))
    if rule.id == "R054":
        applicable = session.message_count >= 30 or int(facts["mixed_task_count"]) >= 3
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"messages={session.message_count}, mixed task signals={facts['mixed_task_count']}",), ("长会话或混合目标会增加误判风险。",))
    if rule.id == "R055":
        applicable = bool(facts["has_large_log"])
        status = "violated" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", ("large log or huge line detected",), ("大日志应该切片读取，而不是全文进入上下文。",))
    if rule.id == "R059":
        applicable = len(corrections) > 0
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"user corrections={len(corrections)}",), ("重复项目事实应写入项目规则。",))
    if rule.id == "R064":
        applicable = session.command_count >= 8 or verification_count >= 3 or len(corrections) > 0
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"commands={session.command_count}, verification signals={verification_count}, corrections={len(corrections)}",), ("重复人工检查应转成 hook、脚本或项目命令。",))
    if rule.id == "R065":
        applicable = bool(facts["has_high_risk_command"]) or bool(facts["has_secret"]) or bool(facts["has_auth_security"])
        status = "satisfied" if applicable and facts["has_automation"] else "partial"
        return _result(rule, applicable, status, "high", ("enforcement-sensitive signal detected",), ("必须强制执行的规则不应只写进提示词。",))
    if rule.id == "R069":
        applicable = bool(facts["has_high_risk_command"]) or sandbox_count > 0
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "high", (f"high-risk command={facts['has_high_risk_command']}, sandbox signals={sandbox_count}",), ("高风险命令必须识别、标记并确认。",))
    if rule.id == "R071":
        applicable = bool(facts["has_secret"])
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "high", ("secret-like text detected",), ("读取或输出 secret 后必须脱敏。",))
    if rule.id == "R078":
        applicable = bool(facts["large_diff_risk"])
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"messages={session.message_count}, commands={session.command_count}",), ("大 diff 应拆分，降低 review 风险。",))
    if rule.id == "R080":
        applicable = error_count > 0
        status = "satisfied" if applicable and facts["has_followup_diagnosis"] else "violated"
        return _result(rule, applicable, status, "medium", (f"error signals={error_count}",), ("失败命令必须产生后续诊断。",))
    if rule.id == "R087":
        applicable = error_count >= 3 or len(corrections) >= 2
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "high", (f"error signals={error_count}, corrections={len(corrections)}",), ("同类规则重复违反时，应从提醒升级到自动化门禁。",))
    if rule.id == "R088":
        applicable = (error_count > 0 or session.command_count > 0) and verification_count == 0
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"errors={error_count}, commands={session.command_count}, verification signals={verification_count}",), ("不能用会话看起来顺利来判断提效。",))
    if rule.id == "R091":
        applicable = error_count >= 2
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"error signals={error_count}",), ("重复失败模式应转成 eval、CI 或脚本化检查。",))
    if rule.id == "R093":
        return _result(rule, True, "satisfied", "medium", ("improvement drafts are created with session evidence",), ("改进建议已绑定会话证据。",))

    if rule.id == "R002":
        applicable = bool(facts["is_bugfix"])
        status = "satisfied" if applicable and _has_any(str(facts["lowered"]), ("repro", "复现")) and verification_count else "violated"
        return _result(rule, applicable, status, "high", ("bugfix signal detected",), ("Bugfix 缺少复现或验证闭环。",))
    if rule.id == "R005":
        status = "satisfied" if verification_count else "violated"
        return _result(rule, True, status, "high", (f"verification signals={verification_count}",), ("没有检测到测试、构建、lint 或验证命令。",))
    if rule.id == "R011":
        applicable = bool(facts["is_deploy"])
        status = "satisfied" if applicable and _has_any(str(facts["lowered"]), ("status", "logs", "health", "journalctl")) else "violated"
        return _result(rule, applicable, status, "high", ("deploy-like task detected",), ("部署任务缺少 status/logs/health 闭环。",))
    if rule.id == "R013":
        applicable = error_count > 0
        status = "partial" if applicable and commands else "violated"
        return _result(rule, applicable, status, "medium", (f"error signals={error_count}",), ("命令失败后需要明确基于错误输出继续诊断。",))
    if rule.id == "R014":
        applicable = error_count >= 5
        status = "violated" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"error signals={error_count}",), ("重复失败说明需要暂停并重新检查假设。",))
    if rule.id == "R015":
        applicable = len(corrections) > 0
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"user corrections={len(corrections)}",), ("用户纠正应沉淀为项目规则。",))
    if rule.id == "R017":
        applicable = bool(facts["has_large_log"])
        status = "violated" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", ("large log or huge line detected",), ("大日志需要切片读取和摘要。",))
    if rule.id == "R019":
        applicable = workflow_count >= 2 or session.command_count >= 20
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"workflow signals={workflow_count}, commands={session.command_count}",), ("多步流程应升级为 skill 或 checklist。",))
    if rule.id == "R020":
        applicable = session.command_count >= 8
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"commands={session.command_count}",), ("重复命令序列应脚本化。",))
    if rule.id == "R024":
        applicable = bool(facts["has_secret"])
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "high", ("secret-like text detected",), ("敏感信息需要脱敏并避免写入输出物。",))
    if rule.id == "R026":
        status = "satisfied" if verification_count else "violated"
        return _result(rule, True, status, "high", (f"verification signals={verification_count}",), ("最终答案需要证据和验证命令。",))
    if rule.id == "R027":
        applicable = verification_count > 0 and session.command_count >= 10
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"test signals={verification_count}, commands={session.command_count}",), ("项目测试命令应写入 AGENTS.md。",))
    if rule.id == "R029":
        applicable = session.command_count >= 30
        status = "violated" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", (f"commands={session.command_count}",), ("工具调用过多时应总结假设并收敛。",))
    if rule.id == "R030":
        applicable = sandbox_count > 0
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "high", (f"sandbox signals={sandbox_count}",), ("高风险或受限命令需要确认和规则化。",))
    if rule.id == "R010":
        applicable = bool(facts["is_ui"])
        status = "partial" if applicable and verification_count else "violated"
        return _result(rule, applicable, status, "medium", ("UI-like task detected",), ("UI 任务需要视觉验证。",))
    if rule.id == "R012":
        applicable = bool(facts["is_review"])
        status = "satisfied" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", ("review-like task detected",), ("Review 应优先关注风险。",))
    if rule.id == "R022":
        applicable = bool(facts["has_auth_security"])
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "high", ("security-sensitive signal detected",), ("安全敏感改动需要更强 review。",))
    if rule.id == "R023":
        applicable = bool(facts["has_dependency"])
        status = "partial" if applicable else "not_applicable"
        return _result(rule, applicable, status, "medium", ("dependency signal detected",), ("新增依赖需要安全和维护风险检查。",))

    applicable = rule.id in {"R001", "R006", "R007", "R008", "R016", "R018", "R021", "R025", "R028"}
    status = "partial" if applicable else "not_applicable"
    severity = "low" if rule.id in {"R008", "R025"} else "medium"
    return _result(rule, applicable, status, severity, ("generic workflow rule",), (rule.description,))


def _result(
    rule: RuleCard,
    applicable: bool,
    status: str,
    severity: str,
    evidence: tuple[str, ...],
    diagnosis: tuple[str, ...],
) -> RuleResult:
    return RuleResult(
        rule=rule,
        applicable=applicable,
        status=status if applicable else "not_applicable",
        severity=severity if applicable else "none",
        confidence=0.8 if applicable else 0.0,
        evidence=evidence if applicable else (),
        diagnosis=diagnosis if applicable else (),
        suggestions=rule.suggestions if applicable else (),
    )


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _is_rule_signal_event(event: TranscriptEvent) -> bool:
    if not event.text.strip():
        return False
    lowered = event.text.strip().lower()
    noise_prefixes = (
        "<environment_context>",
        "<permissions",
        "<collaboration_mode>",
        "<skills_instructions>",
        "cwd=",
        "model=",
        "chunk id:",
    )
    noise_terms = (
        "you are codex",
        "knowledge cutoff",
        "sandbox_mode",
        "original token count",
    )
    if lowered.startswith(noise_prefixes):
        return False
    return not any(term in lowered for term in noise_terms)


def _severity_rank(severity: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 0)

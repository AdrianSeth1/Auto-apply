# AutoApply — 完整项目计划

本文档是 AutoApply 的长文规划参考，用来保留战略、历史路线图背景和阶段设计
理由。它不再承担快速上手或当前验收状态的职责。

为减少重复，文档职责拆分如下：

| 主题 | 权威来源 |
|---|---|
| 当前状态、下一步路线图、验收基线 | `docs/PROJECT_MANAGEMENT.md` |
| 已完成阶段归档 | `docs/PHASE_HISTORY.md` |
| 每个设计选择 / 否决的原因 | `docs/DECISIONS.md` |
| Agent harness 内部细节 | `docs/AGENT_ARCHITECTURE.md` |
| 用户面向的部署 | `docs/DEPLOYMENT.md` |
| 本文 | 战略、历史路线图背景、长文规划说明 |

最近更新：**2026-05-20（Phase 18/19/20 范围细化）**。当前状态、验收基线和下一步路线图以 `docs/PROJECT_MANAGEMENT.md` 为准；本文保留长文规划和历史设计理由。v3.1 在 v3 基础上做了四处校准：
(a) Phase 14 任务队列改用 Celery（不再自建 task model + queue transport + worker runtime；见 D025），APScheduler 也随之退场，由 Celery Beat 承担 cron trigger；
(b) Phase 14 前插入 13.9 子阶段，给所有 Phase 11 及以前的遗留表做一次性 `tenant_id` retrofit migration，把 D020 的"纪律"变成 schema 强制（见 D026）；
(c) HITL gate 后端从单进程文件 JSON 迁到 Celery 任务态 / Postgres 持久化层，避免 Phase 14 多 worker 与 Phase 17 review queue 各自再造（并入 14.x，见 D026）；
(d) Phase 15.3 LaTeX 范围澄清：`src/documents/latex_engine.py` 已存在，Phase 15 不是"从零搭 LaTeX"，而是"加模板包规范 + manifest + adapter"。

2026-05-19 这次刷新记录 Phase 17.9 已完成：provider 层现在覆盖 OpenAI、Anthropic、Gemini、DeepSeek、Moonshot/Kimi、Qwen、xAI Grok、Groq、Mistral、OpenRouter、Ollama、Claude CLI、Codex CLI，以及用户自定义的 OpenAI-compatible provider。同次刷新在 Phase 18（worker 激活 / 可靠性 / 并行 / 清理）和多租户工作之间插入了 **Phase 19**（Per-Posting Tag Cache & Filter Fast Path，重启 2026-05-16 的缓存计划）和 **Phase 20**（用户自定义 Job Sources / Connectors）。多租户与 Auth 加固后移为 **Phase 21**。2026-05-20 进一步细化 Phase 18：worker stub 必须全部收口、任务结果和 DLQ 必须持久化、并行必须带全局/provider 限流、同步 fallback 只做短期调试，垃圾清理必须是自动 quarantine + 审计机制。同日也细化 Phase 19：搜索仍然每次打上游，但 A1 tags 绑定 JD snapshot，A2 score cache 带 profile/scorer 版本，pending/failed tags 降级慢路径而不误 reject。Phase 20 同步加上 URL 安全边界、source 状态机、多源限流/部分失败、受限模板 DSL 和默认关闭的 LLM 模板 feature flag。

---

## 1. 目标

构建端到端的求职自动化系统，覆盖七层能力：岗位获取与过滤、申请人记忆、
简历与求职信定制、快速问题作答、文档处理、表单填写自动化、跟踪与分析。

自 2026-05-12 v2 重规划起保留了商业化野心，并在 2026-05-14 v3 更新中
进一步明确：多租户、Redis 缓存 / 队列传输、分布式锁、按租户配额、
Postgres RLS、后台 worker 模型全部已纳入路线图，即使目前还没有 SaaS 业务层规划。

## 2. 设计原则

1. **状态机驱动。** 每次申请都是一个状态机 —— 可中断、可恢复、可审计。
2. **基于证据的材料生成。** 不做整篇 LLM 重写。Agent 从 profile facts、
   story bank、带标签的 bullet pool 中选择证据，可选地做轻量级 lexical rewrite，
   并由 fact-drift guard 兜底。
3. **两条简历路径。** 需要保留用户原始风格时，patch 用户上传的可编辑源文件；
   从零生成新简历时，以 LaTeX-first 模板包为主。两条路径都要求 LLM 产出结构化
   IR 或 adapter proposal；最终文件由确定性 renderer 负责。
4. **每次提交都人工确认。** 默认在提交前暂停；`--auto-submit` 是可选的逃生口
   且仍要经过 gate queue。
5. **完整审计轨迹。** 截图、DOM 快照、文件版本、QA 应答全部持久化。
   Phase 13 进一步引入按内容哈希的 JD 快照，永远可以追溯某封信 / 某份简历
   是基于哪个 JD 版本生成的。
6. **LLM provider 抽象。** `src/providers/` 之外没有任何 subprocess- 或 REST-
   专属代码。所有调用点统一走 `generate_text()`。
7. **队列管理自动化。** 后台 task 负责调度、重试、幂等和 worker 生命周期。
   Agent 只在单个有边界的 task 内运行并返回结构化结果，不负责 queue ack/nack
   或全局编排。

## 3. 技术栈

| 层 | 技术 | 选择理由 |
|---|---|---|
| 语言 / 运行时 | Python 3.12+，`uv` | 标准的 async + typing 基线 |
| 后端 | FastAPI + Click CLI（`autoapply`） | 同一份代码同时服务 Web + CLI |
| 前端 | Vue 3 + Vue Router + Vite + Tailwind v3 + shadcn-vue + reka-ui | 见 D015 |
| 浏览器自动化 | Playwright（Python，async） | 完整 DOM 访问 + LinkedIn 持久化登录上下文 |
| LLM provider | OpenAI / Anthropic / Gemini，加上 DeepSeek、Moonshot/Kimi、Qwen、xAI Grok、Groq、Mistral、OpenRouter、Ollama、Claude Code CLI、Codex CLI，以及用户自定义 OpenAI-compatible provider；全部在 `ProviderRegistry` 后面 | 见 D016 和 Phase 17.9 |
| Agent harness | 自研，位于 `src/agent/` —— bounded ReAct loop、allow-listed `ToolRegistry`、文件后端 HITL gate、JSON 磁盘 trace store、fixture-driven eval | 见 D017（不用 LangChain / LangGraph） |
| 数据库（权威来源） | PostgreSQL + pgvector + alembic | 匹配用向量检索；alembic 管 schema migration |
| 缓存 / 锁 / 队列（Phase 12+） | Redis 7+ | L2 缓存、分布式锁原语（`SET NX PX`）、任务队列基础设施；见 D018 |
| 任务队列 / 调度（Phase 14+） | Celery 5.x + Redis broker + Redis result backend + Celery Beat（cron trigger） | 见 D025（替换原计划的"自建 queue + APScheduler"），D023 关于 agent/queue 职责切分的原则保留 |
| 文档处理 | python-docx + LaTeX toolchain + docx2pdf / LibreOffice | 原始简历走 DOCX patch；新生成简历走 LaTeX-first；PDF 为衍生物 |
| 配置 | YAML（`config/settings.yaml`、`config/filters.yaml`、`config/companies.yaml`）+ `.env` override | 默认 → 文件 → 环境变量；credential URL 编码 |
| 目标 ATS 平台 | Greenhouse / Lever / Ashby；LinkedIn 用于发现 | 前三家直接 apply；LinkedIn 用 Playwright 持久化上下文做认证 |

## 4. 代码布局（实际情况，不是设想）

```
src/
├── core/                # Config loader、DB session、ORM models、状态机
├── agent/               # 自研 agent harness
│   ├── tools/           #   tool ABC + builtin / browser / profile tools
│   ├── core/            #   bounded ReAct loop + cost telemetry
│   ├── gate/            #   文件后端 HITL approval queue
│   ├── trace/           #   JSON 磁盘 trace store
│   └── eval/            #   fixture-driven eval runner + scorers
├── providers/           # LLM provider 抽象
│   ├── base.py          #   LLMProvider ABC + ProviderKind + AuthType
│   ├── openai.py / anthropic.py / gemini.py   # first-party REST adapter
│   ├── deepseek.py / moonshot.py / qwen.py / xai.py / groq.py / mistral.py / openrouter.py / ollama.py
│   ├── claude_cli.py / codex.py               # subprocess adapter
│   ├── api_base.py      #   共享 REST helper
│   ├── store.py         #   凭据存储（0600 文件 + OS keyring fallback）
│   └── registry.py      #   primary / fallback 分发到 generate_text
├── intake/              # 岗位抓取与 schema
│   ├── greenhouse.py / lever.py / linkedin.py # 适配器
│   ├── schema.py        #   RawJob / JobRequirements / 雇佣类型分类器
│   ├── jd_parser.py     #   LLM-assisted 解析 + 正则 fallback
│   ├── batch.py / search.py / storage.py
│   ├── filters.py       #   YAML-driven filter profile
│   └── search_cache.py  #   文件 JSON 缓存（Phase 13.8 将移除）
├── matching/            # 过滤与打分
│   ├── rules.py         #   硬规则（授权、经验、教育……）
│   ├── semantic.py      #   Embedding + TF 相似度打分
│   └── scorer.py        #   复合打分器 + 质量乘子
├── memory/              # 申请人记忆
│   ├── profile.py       #   identity / education / skills / experiences / projects
│   ├── bullet_pool.py   #   带标签的 bullet，含使用计数
│   ├── story_bank.py    #   STAR 故事 + 主题标签
│   ├── qa_bank.py       #   问题模式 + 标准答案 + 变体
│   └── resume_importer.py # PDF/DOCX → Claude CLI → 结构化 YAML
├── generation/          # 简历 + 求职信 + QA
│   ├── ir.py            #   简历 / 求职信 IR
│   ├── resume_builder.py
│   ├── cover_letter.py
│   ├── fitting.py       #   模板容量 fitting
│   ├── validator.py     #   产物校验（页数、长度）
│   └── qa_responder.py  #   分类器 + 多级 fallback
├── execution/           # 浏览器自动化 + 表单填写 + 提交
│   ├── browser.py       #   Playwright 包装
│   ├── form_filler.py   #   确定性填写器（默认路径）
│   ├── agent_form_filler.py # Phase 9 agent orchestrator
│   ├── file_uploader.py
│   └── ats/             #   按 ATS 的适配器（greenhouse / lever / ashby / generic / base）
├── documents/           # DOCX + PDF + 页数 + 模板
├── tracker/             # CRM：applications 表 + analytics + CSV export
├── application/         # CLI 与 Web 共用的应用层服务
├── cli/                 # Click 命令树（autoapply、init、search、apply、status、provider、web、eval、……）
├── web/                 # FastAPI app factory + JSON API + SPA static mount
└── utils/               # llm.generate_text bridge、rate limiter、logger
```

`src/` 下有 5 个早期占位的空目录仍然存在：`src/applicant/`、`src/cover_letter/`、
`src/filter/`、`src/resume/`、`src/scraper/`，下次清理时建议删掉。

## 5. 数据模型（当前）

当前 Postgres schema 见 `migrations/versions/`（alembic）。核心表：

```sql
jobs (
  id UUID PRIMARY KEY,
  source TEXT,                       -- greenhouse / lever / ashby / linkedin
  source_id TEXT,                    -- 各源的 job id；(source, company, source_id) 是去重键
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT,
  employment_type TEXT,              -- intern / fulltime / coop
  seniority TEXT,
  description TEXT,
  description_embedding vector(1536),
  requirements JSONB,
  visa_sponsorship BOOLEAN,
  ats_type TEXT,
  application_url TEXT,
  raw_data JSONB,
  discovered_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ
);

applications (
  id UUID PRIMARY KEY,
  job_id UUID REFERENCES jobs(id),
  status TEXT NOT NULL DEFAULT 'DISCOVERED',
  match_score FLOAT,
  resume_version TEXT,
  cover_letter_version TEXT,
  qa_responses JSONB,
  screenshot_paths JSONB,
  error_log TEXT,
  state_history JSONB,
  fields_filled INT, fields_total INT,
  files_uploaded JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  submitted_at TIMESTAMPTZ,
  outcome TEXT,                      -- pending / rejected / oa / interview / offer
  outcome_updated_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);

applicant_profile (
  id UUID PRIMARY KEY,
  section TEXT NOT NULL,             -- identity / education / skills / experience / projects
  content JSONB NOT NULL,
  content_embedding vector(1536),
  tags TEXT[],
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

bullet_pool (
  id UUID PRIMARY KEY,
  category TEXT,
  source_entity TEXT,
  text TEXT NOT NULL,
  text_embedding vector(1536),
  tags TEXT[],
  used_count INT DEFAULT 0
);

qa_bank (
  id UUID PRIMARY KEY,
  question_pattern TEXT,
  question_type TEXT,
  canonical_answer TEXT,
  variants JSONB,
  confidence TEXT DEFAULT 'high',
  needs_review BOOLEAN DEFAULT FALSE
);
```

申请状态机有 11 个状态：

```
DISCOVERED → QUALIFIED → MATERIALS_READY → FORM_OPENED
→ FIELDS_MAPPED → FILES_UPLOADED → QUESTIONS_ANSWERED
→ REVIEW_REQUIRED → SUBMITTED → FAILED → NEEDS_RETRY
```

Phase 13 已新增一组用于 **Job Index & Freshness Engine** 的表：

```sql
job_postings        -- 岗位实体（UNIQUE(source, source_job_id)）
job_snapshots       -- 内容版本，content_hash，不可变
search_queries      -- 归一化的搜索条件 + freshness 状态
search_results      -- search → posting 多对多（每次抓取）
refresh_tasks       -- 待抓取的优先级队列
```

再加 `applications.job_snapshot_id` 外键，把每个生成产物钉到具体
JD 版本上。Phase 12+ 所有新表都带 `tenant_id`（在多租户激活之前默认 `"default"` —— 原定 Phase 18，依次顺延到 19、20，目前为 Phase 21），
Phase 13.9 还会给所有遗留表（`jobs`、`applications`、`applicant_profile`、
`bullet_pool`、`story_bank`、`qa_bank` 等）回填同样的列，见 D020 / D026。

## 6. 分层架构

### Layer 1: 岗位获取（Intake）
Greenhouse / Lever / Ashby / LinkedIn 适配器；统一 `RawJob` schema；
LLM-assisted JD 解析 + 正则 fallback；按 `(source, company, source_id)` 去重。
Phase 13 已用 Job Index & Freshness Engine 替换文件 JSON 搜索缓存。

### Layer 2: 匹配与过滤
三层打分：
1. **硬规则**（工作授权、经验上限 + 1 年宽限、教育、雇佣类型、垃圾岗 / 幽灵岗检测）
2. **语义**（description / responsibilities / requirements 上的 embedding 重合 + 在缺 embedding 时退化到 TF 相似度）
3. **风险**（签证、ghost reposting、JD 过稀、缺 apply URL）

复合打分：加权 must-have（70%）/ preferred（30%）技能重合 + 关键词相似度 + 规则加分 × 质量乘子。

Phase 16 加入 reason chain，并为边界分 `[0.4, 0.6]` 启用 edge-case agent。

### Layer 3: 申请人记忆
Profile YAML → DB ingestion，按 section 生成 embedding，bullet 带标签。
`qa_bank` 支持按地区 + 职位类型的变体，并对高风险问题（工作授权、签证、
薪资、入职日期）打 `needs_review`。简历导入器把 DOCX / PDF 转成结构化 YAML
（通过 Claude CLI）。

### Layer 4: 简历 / 求职信生成
结构化 IR + 块状装配。Bullet 按标签重合度从池中选出，可选地在 fact-drift
guard 保护下做轻量级 lexical rewrite（长度比例落在 `[0.3, 2.0]` 之外的会
被拒绝）。求职信生成被约束在四个 section（opening / evidence / 公司挂钩 /
close），250-400 词，不允许编造。快速问题作答按 QA-bank → 模板 → LLM → 标记
review 的级联策略。

Phase 15 把求职信生成升级成 agent（绑定到具体的 `job_snapshot_id`）。

### Layer 5: 表单填写与提交
每次申请是 11 状态的状态机。确定性的 `form_filler.py` 仍是默认路径；
`agent_form_filler.py`（Phase 9）是 agent 路径，按置信度和 HITL 队列把关。
ATS 适配器在 `src/execution/ats/`（Greenhouse / Lever / Ashby / generic）。
Rate limiter 执行随机延时、小时上限、按错误冷却。

### Layer 6: 文件流水线
模板包位于 `data/templates/<document_type>/<template_id>/`，包含 `template.docx`、
`manifest.json`、`style.lock.json` 和样例 IR payload。DOCX 渲染使用 manifest 里
的命名 Word style 加 block marker（`{{resume.sections}}`、`{{cover_letter.body}}`）。
PDF 输出优先 Word + `docx2pdf`，否则降级到 LibreOffice。文件命名是
`{type}_{company}_{role}_{date}.{ext}`；每份产物都有版本号。

### Layer 7: 分析 / CRM
跟踪表记录 source、company、role、date、platform、resume version、match score、
status、outcome、outcome 时间戳。Analytics dashboard 提供 pipeline / outcome /
platform / company 维度的拆分。CSV export 默认排除 `error_log`。

## 7. 已交付的阶段

测试数为各阶段收尾时的快照。当前基线（Phase 10 后）：
**680 通过，1 跳过**（`pytest -q`），`ruff check src/ tests/` clean，
`npm run build` clean。

| Phase | 范围 | 状态 | 测试快照 |
|---|---|---|---|
| 1 | 基础设施 + 申请人记忆 + 文档流水线 | 完成 | — |
| 2 | 岗位获取 + 智能过滤 | 完成 | 156 |
| 3 | 简历/求职信定制 + QA | 完成 | — |
| 4 | 浏览器自动化 + 表单填写 | 完成 | 156 |
| 5 | CLI + 跟踪 + 全流水线 | 完成 | 177 |
| 6 | LinkedIn 集成 | 完成 | 207 |
| 7 | Web GUI（FastAPI + Vue SPA） | 完成 | 228 |
| 8 | Materials 工作区 + DOCX 模板包 + 加固 | 完成 | 340 |
| Agent 8 | Agent Harness（工具 / loop / trace / eval / HITL gate） | 完成 | — |
| Agent 9 | 表单填写 Agent + 成本遥测 + 5-fixture eval | 完成 | 553 |
| 10 | LLM Provider 抽象（REST + subprocess + 凭据存储 + Settings UI） | 完成 | 669 |

每个子阶段的发布记录见 `docs/CHANGELOG.md`。

## 8. 路线图（Phase 11 → 21） —— v3.3，2026-05-19 刷新

v3 在 v1/v2 上修正了四个问题（保留如下）；v3.1 又对 v3 做了四处校准（见本节
开头版本说明）。v3.2 记录 Phase 17.9，并同步 Phase 18/19 的重排。v3.3 进一步
在 Phase 18 和多租户工作之间插入了 Per-Posting Tag Cache（Phase 19）和
Custom Job Sources（Phase 20），多租户后移为 Phase 21。

v2/v3 重规划修正了 v1 草案的四个问题：

1. **PostgreSQL 是权威来源**，不是 SQLite。v1 草案写过 "L2 SQLite cache" 和
   "APScheduler + SQLite jobstore"，两处都错。本项目从来就跑在 Postgres +
   pgvector + alembic 上。（见 D021。）
2. **从 Phase 12 起引入 Redis** 作为缓存 / 锁 / 队列基础设施，为商业化部署
   保留通路。（见 D018。）
3. **自动化批处理需要任务队列。** Phase 14 明确 Redis queue + Postgres task state +
   worker 边界，而不是把后台工作藏在 scheduler 细节里。（见 D023。）
4. **材料生成需要两种简历模式。** Phase 15 现在同时覆盖原始简历 patch 和
   LaTeX-first 生成，而不只是 Cover-letter Agent。（见 D024。）

原先的 "JD scrape caching" 子阶段被升级为完整阶段（**Phase 13: Job Index &
Freshness Engine**），因为这个问题本质是内容版本化 + freshness 状态机 +
审计绑定，不是 KV 过期。（见 D019。）

多租户与 Auth 加固仍然是商业化就绪核心的收尾，但经过三次顺延（原 Phase 18
→ 19 → 20 → 21）现在落在 **Phase 21**。**Phase 18** 是 worker 激活、可靠性、
并行和清理，先把个人版产品打牢。**Phase 19** 重启 2026-05-16 那份"被两次
顺延的"per-posting tag cache & filter fast-path 计划，把搜索缓存从结果集级
下沉到单个 posting。**Phase 20** 是用户自定义 Job Sources（Connectors）——
URL safety 先发，ATS connector 作为 baseline（Greenhouse / Lever / Workday / Ashby /
iCIMS / Smartrecruiters / Eightfold），LLM 模板 DSL 在 feature flag 后面兜底长尾。
Phase 12+ 所有表（包括新的 Phase 19-20
表）仍然从第一天起就带 `tenant_id`。（见 D020 / D026。）

### Phase 11: 可靠性 & 收尾（~1 周）
加固 Phase 10 引入的 provider 层；交付老用户升级所需的 migrate 工具。
- **11.1** `generate_text` 中的 provider fallback 链（primary + 有序 fallback；
  quota / 网络 / auth 失败自动 failover；attempt 链记入 trace）。
- **11.2** `autoapply migrate` CLI：清理 codex-cli credential breadcrumb、
  重命名旧 settings key、检测过期凭据。
- **11.3** 文档同步 —— 把所有文档推到 Phase 10 完成态。
- **11.4** Provider health monitor：`/api/providers/health` 每 5 分钟探测；
  Settings 页 "Last verified" 显示真实遥测。

### Phase 12: 缓存基础设施（~1.5 周）
**首次引入 Redis。** 范围刻意收窄 —— 只做 LLM + embedding 响应缓存。
JD / 岗位内容缓存放到 Phase 13。
- **12.1** `src/cache/` 模块 —— L1 进程内 LRU + L2 Redis；namespace TTL
  （`llm:7d`、`embedding:30d`、`response:5m`）；统一 `get/set/invalidate` API；
  带版本号的 key。
- **12.2** Redis 基础设施 —— 连接池、健康检查、`REDIS_URL` 环境变量、
  `docker-compose.yml`、AOF 持久化、`autoapply redis ping/flush/info` CLI。
- **12.3** 分布式锁原语 —— `with cache.lock(key, ttl)`，基于 `SET NX PX`。
  Phase 13 force-refresh 会用。
- **12.4** LLM 响应缓存 —— `generate_text(cache=True)`；agent loop 默认 False，
  确定性 retrieval 默认 True；命中时累加省钱计数。
- **12.5** Embedding 缓存 —— `embed_text(cache=True)`，30 天 TTL。
- **12.6** Cache 检查 UI `/settings/cache`。
- **12.7** Cost dashboard 升级 —— Phase 9.4 聚合拆 "cached vs fresh" + $-saved 行。

### Phase 13: Job Index & Freshness Engine（~2 周）
用一套合规的 Job Intelligence Database 替换文件后端的
`src/intake/search_cache.py`。
- **13.1** Schema（alembic） —— `job_postings`、`job_snapshots`、`search_queries`、
  `search_results`、`refresh_tasks`；新增 `application_records.job_snapshot_id`
  外键；所有新表带 `tenant_id`。
- **13.2** 归一化层 —— `normalize_search_key()`、`normalize_job_content()`、
  `content_hash()`，hash 时排除不稳定字段（applicant_count、promoted 等）。
- **13.3** Freshness 状态机 `src/jobs/state.py` —— `new → active → stale → unknown
  → expired → archived`。
- **13.4** 搜索流程 —— 默认 cache-first；force-refresh 用 Phase 12 分布式锁
  包住 scrape；失败时保留旧缓存。
- **13.5** 内容版本化的详情 enrich —— scrape → normalize → hash → 当
  `content_hash` 变化时新建 `job_snapshot`；emit `job.content_changed` 事件。
- **13.6** Context-aware freshness —— `should_refresh(job, context)`，context ∈
  {`search_display: 72h`、`generate_materials: 24h`、`before_submit: 6h`}。
- **13.7** Web UI —— "Last updated 18h ago · Refresh"；刷新成功提示
  `N new / N expired / N updated`。
- **13.8** 把历史 `data/cache/linkedin_search/*.json` 迁到 `search_queries` +
  `search_results`；删掉文件缓存模块。
- **13.9** **tenant_id retrofit migration**（Phase 14 开工前必须落地，见 D026）——
  alembic 新 migration 给所有 Phase 11 及以前的遗留表（`jobs`、`applications`、
  `applicant_profile`、`bullet_pool`、`qa_bank` —— `story_bank` 是 YAML，
  `template_packages` 是文件系统模板）加 `tenant_id TEXT NOT NULL DEFAULT 'default'` 列 +
  backfill 现有行；ORM models 同步加字段；现有 query 路径不强制改（保留无过滤的
  全局行为），但 Phase 14 开始所有新代码必须显式带 tenant 上下文。Phase 18 的
  auth middleware 上线后这层"默认 default"的兜底就被 RLS + 中间件取代。**已完成**
  （migration `d8a5c2f1e9b3`，commit `ae46a39`）。

### Phase 14: 任务队列 + 定时工作（~2.5 周，Celery） —— **已完成**

10 个子阶段全部在 `feat/phase-14` 分支上线（commits `83de0db` → `707d94e`）+
两轮 codex review 修复（`3de7084`）。验证基线：1161 passed / 1 skipped；
`ruff check` 干净；前端构建干净；migrations `e1b4f72c8a05`（tasks audit table）
+ `f2c5d83a91b6`（gate_queue）已应用到 dev DB。

改用 Celery 5.x（见 D025）。原计划自建的 task model + queue transport + worker
runtime 全部由 Celery 接管；AutoApply 只在它上面薄薄加一层"agent 边界 + HITL +
trace + tenant 上下文"的 wrapper。D023 关于"queue 拥有执行可靠性、agent 拥有
bounded 决策"的原则保留。

- **14.1** **Celery 接入 + 项目基础**。`celery_app = Celery("autoapply",
  broker=REDIS_URL, backend=REDIS_URL)`、`autoapplyCfg.task_acks_late = True`、
  `task_reject_on_worker_lost = True`、`worker_prefetch_multiplier = 1`（长任务模型，
  不要 prefetch）。task 路由：`search.*` / `materials.*` / `application.*` /
  `maintenance.*` 四个 queue。
- **14.2** **持久化 audit table**（Postgres，权威源）。Celery 的 result backend
  只是 transient，AutoApply 自己维护 `tasks` 表：`id`、`celery_task_id`、`tenant_id`、
  `kind`、`payload`、`idempotency_key`、`status`（`queued/running/waiting_human/
  succeeded/failed/cancelled`）、`attempts`、`parent_task_id`、`trace_id`、
  `created_at`、`finished_at`。Celery signals (`task_prerun` / `task_postrun` /
  `task_failure` / `task_retry`) 自动更新这张表。
- **14.3** **Custom `AutoApplyTask` base class**（Celery `Task` 子类）—— 提供：
  (a) 从 task headers 取 `tenant_id` 注入到 DB session 和 Redis namespace；
  (b) idempotency key 入口检查（已存在 succeeded 记录直接返回）；
  (c) `self.call_agent(...)` 包装：单次 task 内调一次 bounded agent，按结构化
  返回值 (`success` / `failed_retryable` / `failed_terminal` / `needs_human` /
  `needs_followup_task`) 决定 `raise self.retry()` 还是入 gate 还是 enqueue 子
  task；(d) 写 trace 记录。
- **14.4** **HITL gate 后端迁到 DB**（取代单进程文件 JSON，见 D026）。新表
  `gate_queue(id, tenant_id, task_id, kind, payload, status, requested_at,
  decided_at, decision, reason)`；状态 `pending → approved → rejected`。
  Celery task 返回 `needs_human` 时只是把当前 task 状态转 `waiting_human`，
  *不* 阻塞 worker；用户审批后调 `/api/gate/{id}/approve` enqueue 一个 `resume`
  task 重新跑（用同一 idempotency key）。`src/agent/gate/queue.py` 旧 file-backend
  作为兼容层保留一个发布期，然后删除。
- **14.5** **Celery Beat 接入**（取代 APScheduler，APScheduler 完全退场）。
  Beat schedule 在 `src/tasks/beat.py` 声明：`daily_search`、`jd_health_check`
  （驱动 13.3 freshness 时间衰减）、`application_status_sync`、
  `linkedin_cookie_refresh`、`cache_eviction`。Beat 只 enqueue，永远不在 Beat 进程
  里跑业务。多实例 Beat 用 `celery-redbeat` 或 Postgres advisory lock 防双触发。
- **14.6** **Task kinds 实现**：`search.refresh`、`jobs.enrich`、
  `materials.generate`、`application.prepare`、`application.fill`、
  `application.submit`、`status.sync` 各自一个 Celery task；每个走 14.3 的
  `AutoApplyTask` 基类；payload schema 用 Pydantic 模型校验。
- **14.7** **CLI**：`autoapply worker --queues search,materials,apply --concurrency 4`
  （内部 `celery -A src.tasks worker ...`）；`autoapply beat`（启 Beat）；
  `autoapply tasks list/retry/cancel/inspect`（读 14.2 的 audit 表）；
  `autoapply schedule list/pause/run-now`（读 Beat schedule + enqueue 一次性 task）。
- **14.8** **Web UI** `/schedule` + `/tasks` + `/gate`：从 audit 表读 queue depth、
  在跑的 worker（通过 Celery inspect API）、失败原因、手动 retry/cancel；
  `/gate` 取代旧的 agent gate viewer。
- **14.9** **Trace 集成**：`AutoApplyTask.on_success/on_failure/on_retry` 自动写
  trace；child task header 带 `parent_trace_id`，trace viewer 可以从一个 task
  跳到它的 parent/children 链路。
- **14.10** **多实例安全**：Celery 自身保证 task 只被一个 worker 拿到；Beat 多实例
  用 redbeat 的 leader election；advisory lock 兜底（保留 D021 的多实例双触发
  防御原则）。

### Phase 15: Resume & Cover Letter Generation v2（~3 周） —— **已完成**

10 个子阶段全部在 `feat/phase-15` 分支上线（commits `4e95e98` → `439d2d7`）+
一轮 codex review P2 修复（`9b813a3`）。验证基线：1332 passed / 1 skipped；
`ruff check` 干净；migration `a3b9d52e7c41`（source_resumes）已应用到 dev DB。
实现 highlights：

* `src/generation/source_resume.py` —— 上传简历 ingest 管线（DOCX/LaTeX/PDF）
* `src/generation/docx_patch.py` —— 命名样式保留的 DOCX patch 模式
* `src/documents/latex_manifest.py` + `latex_renderer.py` —— manifest-adapter
  LaTeX 渲染（基于已存在的 `latex_engine.py`，不是从零搭）
* `src/generation/materials_router.py` —— patch_existing vs generate_from_template
  调度，每个产物绑定 job_snapshot_id / source_resume_id / template_package_id /
  trace_id
* `src/agent/tools/jd.py` —— jd_lookup agent tool
* `src/generation/agent_cover_letter.py` + `fact_drift.py` —— 五级 fallback
  ladder + 数字漂移阻断
* `src/documents/template_adapter.py` —— 任意 LaTeX 模板 manifest 提案
* 三个 eval suite + 7 个 fixture
* `src/generation/gate_triggers.py` —— 仅持久化 grounding 变更触发 HITL gate
受益于 Phase 12（LLM 缓存）、Phase 13（snapshot 绑定）和 Phase 14（后台材料任务）。
- **15.1** Source-resume model：上传原件按 type、checksum、抽取结构、editability
  flag 存储。PDF 只承诺用于事实抽取，不承诺保格式编辑。
- **15.2** DOCX patch mode：局部修改 summary、bullets、skills 顺序、section 取舍，
  尽量保留原有 styles 和 DOCX 允许保留的布局结构。**降级路径**：当 patch 失败
  （style 找不到、IR 字段映射不上、修改后页数爆掉），自动降级到
  `generate_from_template` 路径，并在 UI / task 结果里告知用户原因，不要让用户
  以为 DOCX 100% 保真。
- **15.3** LaTeX template package 规范。注意 `src/documents/latex_engine.py`
  里编译/渲染原语已存在（Phase 8 期间随 DOCX 模板包一起做的），本子阶段做的是
  *规范化模板包结构*：`template.tex`、assets、`template.manifest.yaml`、sample IR、
  compile engine 选择（`pdflatex` / `xelatex` / `lualatex`）、容量 / 页数规则、
  command / field mapping、escape 规则白名单。重点不是写 renderer，是定义
  manifest schema + 适配器约定。
- **15.4** LaTeX-first resume generator：agent 产出结构化 resume IR；确定性
  renderer（复用已有 `latex_engine.py`）负责 escape、按 manifest 映射、编译、
  校验页数 / 容量。把 `resume_builder.py` 的 LaTeX 分支从"自定义 IR 直转"重构成
  "走 manifest 适配器"。
- **15.5** Materials router：`patch_existing` vs `generate_from_template`，两者都以
  `materials.generate` task 运行，并绑定 `job_snapshot_id`、source/template ID、
  profile version、trace ID。
- **15.6** 共享 `jd_lookup` 工具，供 resume 和 cover-letter agent 使用。
- **15.7** `AgentCoverLetter` orchestrator 输出带 evidence 引用的求职信 IR；现有
  fact-drift checker 作为 post-guard；agent 失败时降级到确定性路径。
- **15.8** Template adapter assistant：agent 可为任意新 LaTeX 模板提议 manifest，
  但持久化前必须 sample compile 通过并由用户确认。
- **15.9** Eval suite 覆盖 DOCX patch fixture、LaTeX template fixture、
  cover-letter fixture。
- **15.10** HITL gate 只在 agent 改 bullet / story bank 或持久化 template adapter
  时触发，不在普通生成时触发。

### Phase 16: Filter Agent + 可解释性（~1.5 周） —— **已完成**

4 个子阶段全部在 `feat/phase-16` 分支上线（commits `203becb` →
`9198a3b`）+ 一轮 codex review P2 修复（`5702da7`）。验证基线：
1398 passed / 1 skipped；`ruff check` 干净；前端 build 干净。

实现 highlights：

* `src/matching/rules.py` —— `RuleResult` 加入 `rule_id` / `verdict` /
  `evidence_excerpt`；每个 hard rule 都从 JD 抽取一段有界
  excerpt（~200 chars，trigger phrase 两侧各 ~80 chars 上下文，
  whitespace 折叠，超长加 ellipsis）
* `src/matching/scorer.py` —— `ScoreBreakdown.job_snapshot_id` +
  `disqualify_results` + `to_dict()`
* `src/agent/tools/score_breakdown.py` —— 只读 dotted-path tool，
  在 agent 实例化时绑定到单个 breakdown
* `src/matching/edge_case_agent.py` —— 只在 `0.4 <= score <= 0.6`
  且非 hard-rule 拒绝时触发；失败一律 fail-closed 走 fallback
  ladder（agent_error / agent_malformed / not_invoked）；
  **永远不会覆盖 hard rules**
* `src/application/matching.py` + `POST /api/matching/explain` ——
  按需重新打分接口，供 popover 调用
* `frontend/src/views/JobsView.vue` —— 每个被过滤掉的 job 卡片上
  加 Info 按钮 + Dialog popover（显示 rule 名、verdict chip、
  reason、evidence_excerpt、snapshot id）
* `tests/agent_evals/fixtures/filter_borderline/` —— 10 个 fixture
  覆盖完整决策矩阵（surface / reject / abstain × agent_ok /
  agent_malformed / agent_error / not_invoked）

（原 plan 保留在下方作为设计说明。）
不替换确定性 filter —— 在其之上加可解释层 + 仅对边界岗位调用 agent。
- **16.1** **`RuleVerdict` 数据结构演进**（这是 schema 改动，不是单纯"加一层"）。
  现状：`src/matching/scorer.py` 的 `ScoreBreakdown.disqualify_reasons` 只是
  `list[str]`，`RuleVerdict` 不带 `evidence_excerpt` / `rule_id` 结构。本子阶段
  要：(a) 把 `RuleVerdict` 改成 `{rule_id, rule_name, verdict, reason,
  evidence_excerpt}` 结构化；(b) 每条规则在 `src/matching/rules.py` 实现里返回
  时主动抽取相关 JD 片段当 `evidence_excerpt`；(c) `ScoreBreakdown` 顶层加
  `job_snapshot_id`，整个打分结果可以钉到具体 JD 版本上。16.3 的 UI 直接消费这
  份结构化输出。
- **16.2** Edge-case agent —— 只对 [0.4, 0.6] 分段调用；用 Phase 8 harness +
  新工具 `score_breakdown`。
- **16.3** Web UI "Why was this filtered?" 按钮。
- **16.4** Eval suite —— 10 个人工标注的边界岗位；agent 决策与人工一致率 ≥ 70%。

### Phase 17: Plan Run Loop + Review Queue（~2 周） —— **已完成**

7 个子阶段全部在 `feat/phase-17` 分支上线（commits `771b6da` → `208db10`）
+ 三轮 codex review 修复（`2d694e9`, `fe11907`, `62c4314`，共 3 个 P1 + 6 个 P2）。
验证基线：1530 passed / 1 skipped；`ruff check` 干净；前端 build 干净；
alembic 升级 dev DB 到 `c9e1f3a7b8d4`。

实现 highlights：

* `src/orchestration/plan_run.py` —— async `run_plan(...)` 编排器，
  依赖注入便于测试。流程：search（cache-first via Phase 13.4）→ score
  （Phase 16 结构化 breakdown）→ top-N qualified → 持久化 review_queue 行
  + 入队 materials.generate + application.prepare。**永不入队
  application.submit**。Pause sentinel 在 search 之前短路。
* 迁移 `b7d9a1e4f3c2` + `c9e1f3a7b8d4` —— `review_queue` 表 + 五态机
  + pending-only partial unique index（同一 snapshot 可以多次走完
  生命周期）。
* `src/application/review.py` —— 单条 + 批量操作 + 状态机守卫。
* `src/web/routes/review.py` —— `/api/review` 路由，tenant 隔离，错误
  映射 (409 / 404)。
* `frontend/src/views/ReviewQueueView.vue` —— 4 列 kanban，stale 行
  在 Pending 列展示 Refresh 按钮（Approve 隐藏），Approved 列有
  Submit + Reject，多选 + 批量操作 + 按公司/标题批量拒绝。
* `src/review/pre_submit_gate.py` —— 6h freshness + snapshot id
  mismatch 检查 + 生命周期态检查；自动 flip 到 stale / rejected。
* `src/orchestration/digest.py` —— 早 8 点 digest，聚合
  `data/plan_runs/*.json` + review_queue 实时计数；dashboard
  banner 渲染 headline。
* `autoapply pause-plan-runs [--clear-pending]` —— sentinel + 暂停
  时批量清空 pending。

（原 plan 保留在下方作为设计说明。）
集成阶段。把 Phase 14（任务队列 + 调度器）+ Phase 13（job-index / freshness）+
Phase 12（缓存）+ Phase 9 / 15（agent）串成 "睡一觉，醒来看 review queue" 的
完整流程。
- **17.1** `plan_run` orchestrator —— search（cache-first，stale 自动刷新）→
  filter（带 16 的可解释性）→ top-N → 入队 `materials.generate` 和
  `application.prepare`；worker 在 task 级 retry/timeout policy 下运行 agent。
  **永不自动提交。**
- **17.2** Review queue 模型 —— `review_queue(id, tenant_id, job_id,
  job_snapshot_id, materials_path, status, ...)`；状态机
  `pending → approved → submitted` 或 `pending → rejected`。
- **17.3** `/review` kanban UI。
- **17.4** 批量操作 —— 多选 approve、按 company / keyword 批量 reject。
- **17.5** 提交前硬 gate —— 重跑 `should_refresh(job, "before_submit")`；
  > 6h stale 则先刷新；岗位已 expired 完全阻止提交。
- **17.6** 早间 digest（08:00）。
- **17.7** `autoapply pause-plan-runs` kill switch。

### Phase 17.8: Material Strategy & Document Library（~1 周） —— **已完成**

补齐用户对材料的控制权：`user_documents` 文档库、上传 / 下载 / promote API、从文档库创建 profile、默认材料策略、plan 级材料覆盖、review 卡片替换材料动作，以及 Materials 页的 Library / Templates / Generate 标签。

### Phase 17.9: LLM Provider Expansion（~0.5 周） —— **已完成**

在 Phase 18 worker 激活前加固 Phase 10 的 provider 抽象，让 provider / model 选择成为设置项，而不是代码改动。

- **17.9.1** 抽出 `OpenAICompatibleProvider`，新增 `ModelInfo`，给一方 provider 加 curated model catalog。
- **17.9.2** 新增 DeepSeek、Moonshot/Kimi、Qwen、xAI Grok、Groq、Mistral、OpenRouter。
- **17.9.3** 新增本地 Ollama provider，支持空 key credential 和 `/api/tags` live catalog。
- **17.9.4** 新增 `GET /api/providers/{id}/models` 和 Settings model picker，保留 custom model 逃生口。
- **17.9.5** 新增 `llm.small_provider` / `llm.small_model` 小模型层，用于 JD parsing、resume import 等抽取任务。
- **17.9.6** 新增 `llm.custom_providers`，用户可无代码接入 OpenAI-compatible proxy、私有 vLLM / LM Studio endpoint 或新上游。

### Phase 18: Worker 激活 / 可靠性 / 并行 / 垃圾清理（~2.5–3 周）

> **重新排序（2026-05-19，再次刷新）**：这一阶段原本是 Phase 19，排在多租户
> 之后。我们把它提到前面了，因为：
> (a) 个人版产品是当前主线；多租户/商业化要等到单用户版本足够稳定再说；
> (b) `data/output/` 的孤儿文件正在累积，清理债现在就在影响日常使用；
> (c) 18.1 的 worker 激活是后续所有 phase（包括多租户）的可靠性/并行/可扩展
>     性前提。
> 现在 Phase 18 之后还有两个产品阶段：**Phase 19**（Per-Posting Tag Cache &
> Filter Fast Path）和 **Phase 20**（用户自定义 Job Sources / Connectors）。
> 多租户 & Auth 加固后移为 **Phase 21**，等个人版功能/质量完整后再做。

一个**修复型 phase**，不是 feature phase。Phase 14 落地了 Celery 骨架（队列、
基类、审计表、可靠性配置、Beat 调度）；Phase 17 在它上面铺了 per-plan 策略 +
review loop；项目 memory 在 2026 年 5 月中旬如实总结了一句话："MQ 骨架在，
肉体不在。"本阶段把肉体填进去，并把 Phase 15 以来累积的清理债一次性还清。

六个支柱，一一对应 Phase 17 收尾 / Phase 18 准备阶段那次 worker 系统审计里
浮出的失败模式：

1. **任务没在队列里跑，且 stub 没收口。** `materials.generate`、
   `application.prepare/fill/submit`、`jobs.enrich`、`maintenance.status_sync`、
   `maintenance.jd_health_check`、`maintenance.linkedin_cookie_refresh`、
   `maintenance.cache_eviction`、`maintenance.gate_expire_sweep` 仍有一批 task
   body 只是 log 一句 "queued" / "tick" 然后 return `"scheduled"` / `"stubbed"`。
   真正的生成跑在 FastAPI 同步请求处理器里，所以用户在 LLM 调用中途关 tab 就
   丢工作；后续 Phase 19 的 content-change tag 触发也会被 stubbed `jobs.enrich`
   卡住。
2. **MQ 可靠性配齐了但没演练。** `task_acks_late=True`、
   `task_reject_on_worker_lost=True`、`worker_prefetch_multiplier=1`、
   idempotency-key 短路、`TaskRecord` 审计行状态机 —— 全部因为 (1) 而未被验证；
   任务成功后的结构化 `result`、DLQ 状态和最后一次尝试时间也没有持久模型。
3. **异步 API 只返回 task_id 还不够。** 前端轮询 `GET /api/tasks/{task_id}` 时，
   不能只知道 succeeded/failed，还要拿到生成产物、application 更新结果和错误摘要，
   否则用户体验会从"同步卡住"变成"异步成功但不知道去哪拿文件"。
4. **并行机会留在桌上，且缺少全局限流。** `rewrite_bullets` 内部串行调 LLM（每个 bullet 一次）；
   resume + cover letter 在一次请求里顺序生成；search 返回 N 条之后的 JD parsing
   也是一条一条 LLM。但只在局部 `asyncio.gather(max=5)` 还不够，多 worker × 多 task
   会把 provider 打爆，必须有全局/provider 级别并发闸门。LinkedIn 详情页抓取**故意**
   串行（反爬契约），不动。
5. **没有自动垃圾清理。** `data/output/` 只增不减；patch 失败时半写的
   `patched_resume_<uuid>.docx` 留下做永久孤儿；每次 form-fill 产生的 screenshots
   一直累积；`TaskRecord` 没有 retention；`delete_document` 是唯一会从磁盘删
   文件的路径。只做手动 dry-run 不够，Phase 18 必须落地每天自动清理。
6. **同步 fallback 容易拖成双路径。** `AUTOAPPLY_SYNC_MATERIALS=1` 可以用于 soak /
   本地 debug，但不能长期保留为和 worker 并行的正式执行路径，否则状态更新、审计和
   artifact 写回会分叉。

**诚实的范围说明**：18.1 是**新建代码**（所有已注册/已调度 task body 收口）。
18.2 是异步 API contract + `TaskRecord.result`。18.3 是在已存在的可靠性基础设施上
演练 + 明确 DLQ 持久模型 + 手动重试 UI。18.4 是新建自动清理系统（不是只做 dry-run）。
18.5 是 `asyncio.gather` / `to_thread` + 全局/provider 限流。18.6 是同步 fallback
的收敛规则。把这六块绑在同一个 phase 里是因为它们面向同一个受众（worker + 操作者），
但内部有顺序依赖：18.4（cleanup）独立、先发，止住当前的失血；18.1/18.2 解锁
18.3 和 18.5；18.6 在异步路径 soak 稳定后收尾。

子阶段：

- **18.1 Worker stub 收口** —— 把所有已注册 / Beat 会调度 / UI 会触发的 stub task
  body 填成真调用链；Phase 18 结束时 `src/tasks/tasks.py` 不应再有"假成功"任务。
  具体：
  - `materials.generate` 端到端调 `generate_material_for_job`，用 Phase 17.8 已
    定型的 `MaterialsGeneratePayload`。生成完用 `regenerate_application_material`
    现在那条路径把 artifact 路径写回 `Application` 行，审计 `state_history` 事件
    形状不变。
  - `application.prepare` / `application.fill` / `application.submit` 的 body ——
    `application.submit` 继续走 Phase 17 的 pre-submit gate；HITL 跳转仍走
    `waiting_human` 审计状态（worker 里没有 `time.sleep`）。
  - `jobs.enrich` 真正调用 `enrich_posting` / content-hash 刷新链路，保证 Phase 19
    的 `posting.tag` on-content-changed 触发有可靠上游。
  - `maintenance.gate_expire_sweep` 扫描过期 gate 并置 `expired`；
    `maintenance.jd_health_check` 驱动 freshness 状态衰减；
    `maintenance.cache_eviction` 改为 artifact cleanup 入口；
    `maintenance.linkedin_cookie_refresh` 至少做 session health probe 并把失败写进
    task error / UI；`maintenance.status_sync` 若没有真实实现，必须显式返回
    `not_implemented` 且不被 Beat/UI 当成成功。

- **18.2 异步 API + task result** —— 长任务 API 不再同步阻塞，并让轮询端点能读到
  成功产物。
  - 异步 REST 表面：`POST /api/jobs/generate-material` 和
    `POST /api/applications/{id}/regenerate-material` 切到"enqueue 后返回
    `task_id`"，配合 `GET /api/tasks/{task_id}` 轮询端点（`TaskRecord` 背书）。
    SPA 加一个通用"长任务" hook，现有 view 不用每个都写一遍 polling 样板。
  - `tasks` / `TaskRecord` 增加 `result JSONB`（或等价持久字段），worker 成功时写入
    `application_id`、`resume_path`、`cover_letter_path`、promoted document id、
    trace id 等结构化结果；`GET /api/tasks/{task_id}` 返回 `result`。
  - 任务失败时保留短错误摘要在 `last_error`，完整 trace 仍走 trace store。
  - **测试**：端到端测试用 `apply_async` 对着 in-process Celery worker 触发
    `materials.generate`（**不**用 `task_always_eager=True` —— 我们要的是真
    broker contract）。

- **18.3 可靠性演练 + DLQ + 手动重试** ——
  - 加 `tests/test_worker_resilience.py` 测试套：在任务半路 `os.kill(pid,
    SIGTERM)` 一个 Celery worker 子进程，断言任务以同样的 `idempotency_key`
    被恰好重入队一次。Poison-message 处理同样测一遍。
  - 死信队列（DLQ）：Postgres 是事实来源，建议新增 `dead_lettered` 状态以及
    `last_attempted_at`、`dlq_reason`、`dead_lettered_at` 字段（或独立
    `task_dead_letters` 表，但必须可从 `/api/tasks` 查询）。耗尽 `max_retries=3`
    的任务进入 `dead_lettered`，不再被审计行的 `failed` 状态默默吸收。
  - Redis per-kind DLQ 可以作为 broker 层实现细节，但 UI / audit / retry 不依赖 Redis
    留存；从 DLQ 重试时拿原 payload 创建新任务、新 idempotency_key，原失败行永久保留。
  - SPA `/tasks` 加一个"卡住 / 失败"标签页，列 DLQ 条目，带 payload 预览 +
    重试 / 丢弃操作。

- **18.4 自动 artifact cleanup + quarantine + 手动工具** ——
  - `docs/DECISIONS.md` 加 D028："`data/output/` 是 cache，不是 vault；用户资产由
    引用索引保护"。写代码前先 review artifact 分类、retention、quarantine 和恢复策略。
  - 新增 `src/maintenance/artifacts.py`：每次清理先从数据库构建 protected set，包含
    `Application.resume_version` / `cover_letter_version`、`user_documents.storage_path`、
    `source_resumes.storage_path`、review/gate/task payload 和 `TaskRecord.result` 中的
    artifact path、模板包路径、profile/source resume 原件等。protected path 永不被普通
    自动清理删除。
  - 文件分类规则：`protected` 永不删；`tmp` / `.part` / 半写文件 24h 后清；
    `failed_artifact` 24-72h 后清；`orphan_output` 超过
    `cleanup.output_retention_days=30` 后清；soft-deleted application artifact 宽限
    `cleanup.soft_deleted_retention_days=14` 后清；screenshots 每个 application 保留最近
    5 张，其余归档或清理；成功 task 30 天归档，失败 task 90 天，`waiting_human` 不过期。
  - 自动任务默认启用安全删除：`maintenance.cache_eviction`（或新别名
    `maintenance.artifact_cleanup`）每天跑，流程是 scan → protected-set 校验 → candidate
    分类 → move 到 `data/quarantine/<run_id>/...` → 写 cleanup report → 清理超过
    `cleanup.quarantine_days=7` 的 quarantine。自动清理必须真的把孤儿移出
    `data/output/`，不是只 dry-run。
  - 新增 `cleanup_runs` / `cleanup_items` 审计表（或等价持久报告）：记录 run id、mode、
    action、reason、path、size、mtime、bytes reclaimed、quarantined/deleted/error 计数、
    restore 状态。每次自动清理都能回答"删/隔离了什么、为什么、能否恢复"。
  - CLI：`autoapply cleanup scan` 只报告；`autoapply cleanup clean` 立即按同一规则执行；
    `autoapply cleanup restore <run_id> <path>` 从 quarantine 恢复；
    `autoapply cleanup purge-quarantine` 清超过宽限期的 quarantine。手动工具是自动清理的
    操作面，不是替代品。
  - 原子写 helper：`with atomic_write(target_path) as tmp` 上下文管理器，写到
    `target_path.with_suffix(target_path.suffix + ".tmp")`，成功 rename、异常 unlink。
    在每个 `generate_*` / `patch_*` / `_copy_library_document_to_output` 调用点套上，
    保证崩溃不会在硬盘上留半写的 DOCX/PDF。
  - `Application` 删除 API + UI —— `DELETE /api/applications/{id}`，默认软删（置
    `Application.deleted_at`）；`cascade=true` 只把符合清理规则的关联 artifact 送入
    quarantine，永久删除仍等 quarantine 宽限期。

- **18.5 战略性并行 + 全局/provider 限流** ——
  - `rewrite_bullets` 改成 `asyncio.gather` 调 `_rewrite_single_bullet`，并发
    上限 5（受 provider rate-limit 约束）。预期：10 个 bullet 的简历 30s → 6s。
  - `_generate_selected_material` 对单个 job 通过 `asyncio.to_thread` 并行
    跑 `generate_resume` 和 `generate_cover_letter`（两者目前都是 sync；用
    `to_thread` 保留 body 不动）。预期：双文档场景 75s → 45s。
  - `intake.jd_parser.parse_requirements_batch()` 新 helper，接受 N 条
    description 并发跑，受同样的速率上限管。从 search 后处理调用（`use_llm=True`
    时）。预期：25 条 × 3s/parse = 75s → 15s。
  - **故意不做**：并行化 LinkedIn 详情页抓取。`enrich_with_details` 现在的
    串行 + 随机延迟循环是反爬契约，本阶段内不动。
  - 配置分层：`parallelism.bullet_rewrites.max_concurrent_per_task=5`、
    `parallelism.llm.max_concurrent_global=10`、
    `parallelism.provider.<id>.max_concurrent=N`。所有 LLM 调用经过同一限流器；429 时
    走 provider-aware backoff，而不是让每个任务各自重试打爆上游。

- **18.6 同步 fallback 收敛** ——
  - 现有同步端点只保留在 feature flag `AUTOAPPLY_SYNC_MATERIALS=1` 后面做短期 soak /
    本地 debug；默认走异步。
  - UI 不暴露同步路径；Phase 18 验收后删除同步 fallback，或至少把它标成 dev-only，
    防止长期维护两套材料生成状态机。

排序逻辑：18.4 先发（孤儿现在就在堆积，跟 MQ 状态无关，而且自动 quarantine 能立即止血）。
18.1/18.2 紧接（解锁 18.3、18.5，并修掉"关 tab 丢工作"那个问题）。18.3 和 18.5
之后并行推（动的是不同文件）。18.6 在异步路径 soak 稳定后收尾。

延后到后续 phase 的未决问题：
- 持久任务进度 UI（实时 SSE 流式，不是轮询）。Phase 18 只做 polling。
- 给未来 ops dashboard 用的跨租户 DLQ surfacing。
- 反爬 session pool —— 路由到 N 个独立 session 就能让 LinkedIn 详情页并行
  变安全。本阶段不做（和 Phase 20 Tier 2 的风险有重叠）。

### Phase 19: Per-Posting Tag Cache & Filter Fast Path（~2 周）

> **历史**：这份计划最早是 2026-05-16 排上的 Phase 19（"Per-Posting Tag
> Cache & Filter Fast Path"），先后被 17.9 LLM Provider Expansion 和 18/19
> 多租户重排挤掉过两次。这次刷新把它放回 Phase 19，排在 Custom Sources 之
> 前，因为多个 source 同时返回同一个 posting 时缓存价值会被放大。

把搜索缓存的颗粒度从"结果集"下沉到"单个 JD snapshot / posting 分析结果"。当前的 `search_results`
TTL 短路会让一个结果集在 1 小时内整体生效 —— 这意味着 profile 编辑在 TTL
窗口内会悄悄隐藏我们已经付费抓回来的 posting，而上游新出的 posting 在 TTL
过期之前都看不到。Phase 19 反过来：搜索每次都重新拉，但每个 posting 的
**客观属性**按 JD snapshot 只算一次（A1），每个 snapshot 的**按 profile / scorer
版本评分**缓存复用（A2）。搜索每次打上游是有意选择；Phase 19 保留这个激进刷新策略，
先解决"新岗位被 TTL 掩盖"的问题，等真实遇到 LinkedIn 限流 / cookie 失效再做降级策略。

**子阶段：**

- **19.1** Schema migration。A1 tags 的事实来源放在 `job_snapshots`，不是只放在
  `job_postings`：`tags JSONB DEFAULT '{}'`、`tagger_version INT DEFAULT 0`、
  `tags_status TEXT`（`pending` / `computing` / `ready` / `failed`）、
  `tags_computed_at TIMESTAMPTZ`。`job_postings` 可以保留 latest-tag denormalized
  字段供 JobsView 快速展示，但历史解释和 fast-path 判定必须读 snapshot 级 tags。
  新表 `job_posting_scores`（`tenant_id`、FK `posting_id`、FK `snapshot_id`、
  `profile_id`、`profile_version`、`scorer_version`、可选 `agent_version` /
  `model_id`、`score_breakdown JSONB`、`verdict TEXT`、`computed_at`）。唯一键至少是
  `UNIQUE (tenant_id, snapshot_id, profile_id, profile_version, scorer_version)`，
  防止规则/agent 升级后误复用旧 verdict。索引至少覆盖
  `(tenant_id, snapshot_id, profile_id, profile_version, scorer_version)`、
  `(tenant_id, profile_id, computed_at)`、`(tenant_id, verdict, computed_at)`。
  新列 / 新表从第一天起就带 `tenant_id`（D026）。
- **19.2** `src/jobs/tagger.py` —— 纯函数规则，只读 JD snapshot，不读 profile：`work_mode` / `level` /
  `sponsorship_signal` / `intern_eligible` / `posting_age_bucket` /
  `clearance_required` / `usa_only`。A1 只描述客观岗位属性，不能产出
  `good_match` / `worth_applying` / `high_priority` 这类主观判断；这些属于 A2 score。
  模块级 `TAGGER_VERSION` 常量，bump 时触发可控 retag。
- **19.3** 新增 `posting.tag` Celery task kind + `enrich.on_content_changed`
  监听器自动 enqueue，每次 snapshot content-hash 变化都打一次新标。新增
  `posting.tag_backfill` 分页后台任务：每批处理 100/500 个 `tagger_version < TAGGER_VERSION`
  的 snapshot；UI 显示"正在打标"banner；backfill 未完成时 fast-path 降级到慢路径，
  不阻塞普通搜索。
- **19.4** `job_posting_scores` write-through：Phase 16 的 Filter Agent
  把算出的 verdict 按 `(tenant_id, snapshot_id, profile_id, profile_version,
  scorer_version)` 写回；读路径只复用当前 `profile_version` + 当前 `scorer_version`
  命中的行，规则/agent/prompt 版本变化自然冷启动。旧 score 保留用于审计，但 UI 默认
  不扫旧版本。
- **19.5** `cached_search` 重构：去掉 TTL 短路，保留 `search_results` 行
  （供"消失对比"和分页用），保留分布式锁（防同 source 并发刮取）。行为明确：
  每次搜索都打上游；如果未来真实遇到 LinkedIn 限流 / cookie 失效，再另开阶段做
  source-aware 降级策略。
- **19.6** Filter fast-path（`src/filter/fast_path.py`）：A1 硬规则先 reject，
  A2 缓存 score 命中就复用，未命中再 enqueue 真 Filter Agent。重要 fallback：
  `tags_status in ('pending', 'computing')` 时展示 posting + `Tagging...`，但不使用
  tags 做 reject；`tags_status='failed'` 时走普通 scoring / 人工 retag，不默认过滤。
  Plan-run picker 和 Jobs view 都走这条路径。
- **19.7** 前端：JobsView 给每条 posting 加 tag chip，`tags_status='pending'`
  时显示 spinner，加手动 `POST /api/jobs/postings/{id}/retag` 按钮；
  ReviewQueueView 上标 `(cached score · profile vXYZ · scorer sABC)`，让用户分得清这次
  verdict 是缓存的还是新算的。
- **19.8** 文档：README / PROJECT_MANAGEMENT / CHANGELOG；新加一条
  Decision 记录 A1+A2 拆分、snapshot 级 tags、`profile_version = sha256(canonical_json(profile))[:12]`
  的派生、`scorer_version` 缓存键，以及 Phase 19 不承诺跨 source canonical dedupe。

**需要明示的行为变化**：搜索不再用 TTL 短路了，每次搜索都打上游。这个改变
有合理性 —— 之前那个 TTL 在掩盖新 posting；per-posting 缓存把*分析*热路径
留住，不再把*抓取*热路径留住。

**边界说明**：Phase 19 避免的是同一 snapshot + 同一 profile/scorer 版本的重复评分。
跨 source 的同一岗位（LinkedIn、Greenhouse、公司官网各抓到一份）如果产生不同
`job_posting` / `job_snapshot`，本阶段不承诺自动复用 score。可以先加
`canonical_fingerprint`（company + title + normalized location + application_url）为 Phase 20+
铺路，但完整 cross-source canonical dedupe 不放进 Phase 19。

**`TAGGER_VERSION` 提升在大库上代价不低。** Retag enqueue 走分页后台任务，
UI 期间显示"正在打标"的 banner；未完成期间 fast-path 降级慢路径，不误 reject。
只要 bump 是罕见事件就 OK。

**Score cache 是增长型表。** 旧 `profile_version` / `scorer_version` 行保留用于审计，
但查询默认只看当前版本；成功行可按月归档到冷表或压缩摘要，避免热表无限增长。

### Phase 20: 用户自定义 Job Sources / Connectors（~3–3.5 周）

> **历史**：2026-05-19 在 Phase 19 重启之后，紧接着的架构 review 把这块明确
> 列为下一个产品 phase，并在同一天敲定了"ATS 先发、LLM 模板兜底"的两层结构。

让用户能在 LinkedIn 和内置 ATS intake 之外，添加公司自家的招聘站（Nvidia、
Microsoft、Stripe 等）。本阶段的核心不是"让 LLM 通杀网页"，而是安全、可维护地
接入用户指定 source：先做 URL 安全边界和 ATS connector baseline，再把 LLM 模板
放在 feature flag 后面作为长尾兜底。

**范围原则**：Tier 1（URL safety + ATS detection + connector registry + 多源搜索）是
Phase 20 必须交付；Tier 2（LLM scraper templates）默认关闭
`custom_sources.llm_templates.enabled=false`，可作为 20.x/后续 phase 单独稳定，不能拖住
Tier 1 发布。

**20.0 —— Source URL Safety（先发，阻断 SSRF / 内网探测）：**

- `POST /api/sources` 任何 fetch / Playwright 打开页面之前先过 URL guard：只允许
  `http://` / `https://`；拒绝 `file://`、`ftp://`、`data:` 等 scheme；拒绝
  localhost、`127.0.0.1`、`0.0.0.0`、内网 IP、metadata IP（如 `169.254.169.254`）。
- redirect 最多 5 次，每次跳转后重新校验目标 host/IP；限制响应大小、总耗时、DNS 解析
  结果和下载类型；错误以可读原因返回 UI。
- Playwright 域名锁：模板执行期间只能访问同一注册域名或已识别 ATS 域名，禁止任意
  cross-domain navigation、download、file upload、form submit、arbitrary JS evaluate。

**Tier 1 —— ATS connector 框架 + 多源搜索（~1.5–2 周）：**

- **20.1** Source / Connector 数据模型。区分 `Connector`（能力定义，如 Greenhouse、
  Lever、Workday、LinkedIn、TemplateConnector）和 `JobSource`（用户配置实例，如 Nvidia
  careers）。新增 `job_sources` 表：`id`、`tenant_id`、`display_name`、`url`、
  `connector_kind`、`ats_type`、`status`、`health_status`、`last_probe_at`、`last_error`、
  `created_by`、`created_at`、`updated_at`。不要同时保留含义不清的 `owner_tenant_id`；
  多租户隔离字段就是 `tenant_id`（D026）。
- **20.2** ATS 指纹检测器（`src/intake/ats_detect.py`）：在 20.0 URL guard 后跟随重定向 + DOM
  fingerprint 识别 careers URL 背后的 ATS。首发覆盖：Greenhouse、Lever、
  Workday、Ashby、iCIMS、Smartrecruiters、Eightfold。识别失败 → connector
  停在 `draft` 直到 Tier 2 推断模板。
- **20.3** 把现有 LinkedIn / Greenhouse / Lever / Workday adapter rewrap 成
  注册 Connector，搜索分发走 registry 而不是硬编码 `source` 字符串。`Connector` ABC
  统一 `fetch_jobs(source_config) -> list[RawJob]`，fixture 测试覆盖 detect / fetch /
  normalize / dedupe key。
- **20.4** Add-source UX + 状态机：`POST /api/sources` 跑一次安全校验、检测和验证 fetch，
  成功才进入 `active`。新增 "Sources" 页面（同 17.9 provider 列表的形状：Connected
  vs Available、健康徽章、手动 probe / disable / disconnect / clear session）。状态机：
  `draft`、`probing`、`active`、`degraded`、`needs_review`、`disabled`、`deleted`。
  只有 `active` 默认参与搜索；`degraded` 低频重试；`needs_review` 不自动跑；
  `disabled` / `deleted` 不跑。
- **20.5** 多源搜索：`SearchPayload.sources: list[str]`，Celery group 按
  source fan-out，但不是无限并发。配置：`sources.max_concurrent_per_search=5`、
  `sources.per_source_min_interval_minutes=10`、`sources.timeout_seconds=30`、
  `sources.max_pages_per_source=3`、`sources.max_jobs_per_source=100`。单个 source 失败
  返回 partial result 并更新 source health，不让整次搜索失败。Plan-run 表单加 source
  多选；plan 持久化 `source_ids`，Beat 每次读它。
- **20.5b** 最小 cross-source dedupe 边界：Phase 20 不做完整 `canonical_job_id` 合并，
  但新增 `canonical_fingerprint`（`normalized_company` + `normalized_title` +
  `normalized_location` + `canonical_application_url`）用于 UI 标注 `possible duplicate`。
  scoring 仍按 Phase 19 的 snapshot cache 走；真正 canonical merge 留后续 phase。

**Source session / credential 隔离：**

- 需要登录态的 source 使用 per-source `storage_state`：
  `data/sources/{tenant_id}/{source_id}/storage_state.json`。cookie 不进 `job_sources` JSONB；
  UI 提供清除 session；source 删除时 session 进入 quarantine / 删除。Phase 21 再接真实
  tenant credential store。

**Tier 2 —— LLM 辅助 scraper 模板（~1.5 周）：**

- **20.6** 模板 schema + executor：`scraper_templates` 表（`selector_recipe: jsonb`、
  `allowed_steps: jsonb`、`health: jsonb`）。模板不是任意 Playwright 代码，而是受限 DSL：
  `start_url`、`job_card_selector`、`title_selector`、`company_selector`、
  `location_selector`、`application_url_selector`、`next_page_selector`、`max_pages`。
  允许 step 只有 `goto`、`wait_for_selector`、`click_next`、`scroll`、`extract`；禁止
  arbitrary JS、form submit、file upload、download、跨域跳转。
- **20.7** LLM 模板推断：默认 feature flag 关闭。ATS 检测失败时，Playwright 把页面
  （HTML + 截图）抓回来，走 `generate_json(tier="small")` 让 LLM 产出 DSL 候选，不产出
  浏览器脚本。候选模板必须经过 preview：展示前 5-10 个岗位、原网页链接、每个字段来自
  哪个 selector；用户确认字段映射正确并点击 Activate 后才变 `active`，否则保持
  `needs_review`。
- **20.8** 模板 self-heal：每个 source 健康探测（沿用 Phase 11.4
  `src/providers/health.py` 的模式）数连续失败次数，过阈值就 queue 一次
  LLM 重新推断；新 recipe 跟旧的差异过大就把 source 标 `needs_review`
  而不是自动跑。

**测试要求：**

- `tests/fixtures/connectors/{greenhouse,lever,workday,ashby,icims,...}/` 固化 HTML / JSON
  fixture；CI 不依赖真实网站。
- 覆盖 ATS detect、`fetch_jobs`、RawJob normalize、dedupe key、source health、partial
  failure、URL guard、redirect guard、template DSL executor、bad selector preview。

**风险预案：**

- **反爬**（Cloudflare / Akamai JS challenge）。Tier 1 只承诺 ATS 背后的
  站点（这些都不上 bot challenge）；Tier 2 不承诺通杀，必要时挂住宅代理。
- **登录墙** —— Tier 2 v1 不做自动登录；用户可手动认证并把 session 存在 per-source
  storage_state，UI 可随时清除。
- **LLM 成本** —— HTML 输入动辄 20k+ token。激进缓存 + 默认 `tier="small"` +
  per-source token budget，防失控模板烧账单。
- **维护负担** —— 模板会腐烂，self-heal 循环是必要的，没它 Tier 2 会变成
  墓地。
- **安全** —— 用户输入 URL、redirect、Playwright、LLM 生成 selector 全部走 20.0 guard
  和受限 DSL；任何绕过 URL guard 的 fetch 都是 P1。
- **法律 / ToS** —— 用户对添加的每个 careers 站点的 ToS 自负。AutoApply
  不打包默认公司列表，全靠用户主动添加。

### Phase 21: 多租户 & Auth 加固（~2.5 周，已推迟）

> **重新排序历史**：原本是 Phase 18（17.8 之后的下一步）；2026-05-19 重排
> 推到 Phase 19；同次刷新插入了 Per-Posting Tag Cache 后又推到 Phase 20；
> 同次插入 Custom Sources 后又推到 Phase 21。Phase 13.9 打下的 schema
> `tenant_id` 基础以及 Phase 19-20 新增表（`job_posting_scores`、
> `job_sources`、`scraper_templates`）继续保持的 `tenant_id` 纪律，
> 让激活成本一直保持在可控范围。

激活 Phase 12-20 散布的商业化就绪工作。SaaS 业务层（计费、注册流、营销页）
**不在范围内** —— 本阶段只让现有系统能安全托管多个隔离用户。

**诚实的范围说明**：13.9 已经把 schema 层的 `tenant_id` 列补齐了，Phase
19-20 的新表（`job_posting_scores`、`job_sources`、`scraper_templates`）
也继续保持这一纪律，所以"加列 + backfill"的部分确实不是重写。但下面这几
块**实质是新建**，不是"激活已有工作"：21.2 auth middleware（`src/web/`
目前完全没有 auth 层）、21.4 Redis namespace 重构（现在 key 是
`{version}:{namespace}:{key}`，没有 tenant 前缀，需要全局改 wrapper）、
21.7 凭据存储（`src/providers/store.py` 目前是单文件全局 JSON，需要按
租户切目录 + keyring entry 重命名）。真正"激活"的只有 21.1 / 21.3 /
21.5 / 21.6。

- **21.1** `tenants` + `users` 表；把 13.9 留下的 `tenant_id='default'` 行接到
  真实租户上。
- **21.2** **从零做** FastAPI auth middleware —— session/token 解析、
  `current_tenant_id` 注入到 `ContextVar`；ORM session 通过 SQLAlchemy event
  自动在 query 上拼 `tenant_id = :current_tenant`；Celery task headers 自动带
  租户上下文（14.3 已经预留接口）。
- **21.3** Postgres Row-Level Security policy —— DB 层兜底，防 ORM 漏过滤。
- **21.4** **重构** Redis key 命名 —— 所有 namespace 前面加 `tenant:{id}:` 前缀；
  `src/cache/base.py` 的 key 构造改为强制注入租户上下文（无上下文则抛错而不是
  fall back 到 default）。
- **21.5** 按租户的配额（LLM token、scrape 速率、存储）。超限返回 429。
- **21.6** Audit log 表 —— `audit_events`（提交、设置变更、凭据操作、手动调度
  触发）。append-only。
- **21.7** **重构** 凭据存储 —— `src/providers/store.py` 从单文件全局 JSON 切到
  `data/tenants/{id}/credentials/`，keyring entry 命名加租户前缀；migrate 现有
  `data/providers/credentials.json` 到 `default` 租户。

### 时间表

截至 2026-05-19：Phase 1-17.9 已落地（`dev`）；下一个要做的是 Phase 18
（worker 系统审计之后重排过的优先级）。

| Phase | 范围 | 工时 | 状态 |
|---|---|---|---|
| 11 | 可靠性 & 收尾 | 1 周 | 已完成 |
| 12 | 缓存基础设施（Redis） | 1.5 周 | 已完成 |
| 13 | Job Index & Freshness Engine | 2 周 | 已完成 |
| 13.9 | tenant_id retrofit migration | 0.3 周 | 已完成 |
| 14 | 任务队列 + 定时工作（Celery） | 2.5 周 | 已完成（task body 是 stub —— 在 18.1 激活） |
| 15 | Resume & Cover Letter Generation v2 | 3 周 | 已完成 |
| 16 | Filter Agent + 可解释性 | 1.5 周 | 已完成 |
| 17 | Plan Run Loop + Review Queue | 2 周 | 已完成 |
| 17.8 | Material Strategy & Document Library | 1 周 | 已完成 |
| 17.9 | LLM Provider Expansion | 0.5 周 | 已完成 |
| **18** | **Worker 激活 / 可靠性 / 并行 / 垃圾清理** | **2.5–3 周** | **下一步** |
| 19 | Per-Posting Tag Cache & Filter Fast Path | 2 周 | 已规划 |
| 20 | 用户自定义 Job Sources（Connectors）—— URL safety + ATS 检测 + 多源搜索 + 模板 DSL | 3–3.5 周 | 已规划 |
| 21 | 多租户 & Auth 加固 | 2.5 周 | 已推迟（等个人版成熟后再做） |

个人版产品到 Phase 17.9 已 feature-complete。Phase 18 把它做硬（真 worker、
retention、并行）；Phase 19 把搜索缓存模型换掉，让同一 snapshot + 同一 profile/scorer
版本不会重复评分；
Phase 20 把"用户自定义公司招聘站"这个能力打通；Phase 21 才激活 Phase 12-20
一路留着的多租户底座。Phase 18 是在 Phase 17 收尾时做完一次 worker 系统
审计之后确定的 —— 那次审计发现 task body 都是 stub、没有 cleanup 策略、
并行机会从未被探索过。Phase 19 重启了 2026-05-16 排上但被 17.9/18 重排
挤掉两次的缓存计划。Phase 20 承接"我就要 Nvidia"那类用户诉求，做成两层
架构。Phase 21 已经被推迟了四次（18 → 19 → 20 → 21）—— 每次推迟都让
schema 层的 `tenant_id` 纪律继续保持，最终激活成本可控。

## 9. 横切质量基线

Phase 11 起强制执行：

- **测试** —— 任何 PR 都不能让套件低于当前 680 个通过。
- **Lint** —— `ruff check src/ tests/` 保持 clean。
- **每个子阶段 codex review** —— commit 前跑 `codex review --uncommitted`；
  P1 finding 阻止合并。
- **成本上限** —— 任何 eval suite 把总成本推到 $1.00 / 100 case 之上都要
  显式给理由。
- **文档同步** —— `docs/PROJECT_MANAGEMENT.md` + `docs/CHANGELOG.md` 在每个
  Phase 收尾时更新，不要攒一批。
- **多租户卫生**（Phase 12+） —— 每张新表带 `tenant_id`；每个新 Redis key
  带前缀；每个新后台任务接收 tenant 上下文。零例外，否则最终的多租户阶段
  （现 Phase 21）会变成重写。

## 10. 验收清单（按 Phase 的 smoke）

| Phase | Smoke 命令 / 观察项 |
|---|---|
| 1 | 加载 profile YAML → 入库 → 生成一份定制 Word resume + PDF |
| 2 | 从 Greenhouse 抓岗位 → 打分排序 → top-N |
| 3 | 给定 JD → 自动选 bullet → 定制 resume + CL + 快速问题作答 |
| 4 | 给一个 Greenhouse 岗位 → 自动填表 → 上传文件 → 截图（不提交） |
| 5 | 10 条岗位跑全流水线 → 看跟踪 dashboard → 分析报告 |
| 6 | LinkedIn 搜索 → 外部 ATS 链接解析 → 接入现有 apply / material 流水线 |
| 7 | `autoapply web` → Vue SPA 搜索 / 跟踪 / 设置 |
| 8 | `/jobs` → `/materials?jobId=...` → DOCX/PDF 生成、预览、校验、下载 |
| Agent 8 | `autoapply eval --suite agent_smoke` → 全部 case 通过 |
| Agent 9 | `autoapply eval --suite form_filler --min-pass-rate 0.85` → 5/5 通过，估计成本 ≤ $0.25 |
| 10 | Settings 页 → 连接 / 测试 / 断开每个 provider；`autoapply provider test <name>` 报真实 auth 状态 |
| 11 | 中途 revoke primary provider → fallback 链生效 → eval 仍通过；`autoapply migrate` 清理遗留状态 |
| 12 | 同 batch 跑第二次 → LLM cache hit-rate > 80%、wall time < 20%、cost < 5%；Redis 重启后 L2 entry 恢复 |
| 13 | 同搜索条件二次访问 < 2s（无 HTTP）；岗位内容变了产生新 `job_snapshot`；revoke LinkedIn cookie → 旧缓存仍可展示 |
| 13.9 | alembic upgrade → 所有遗留表带 `tenant_id='default'` 列；现有 query 路径不变（无回归） |
| 14 | `autoapply worker -Q materials` 起 Celery worker；入队 100 个混合 task → 按 queue 路由分发；杀 worker → `task_acks_late + task_reject_on_worker_lost` 自动重入队一次；Celery Beat 触发 `daily_search` 只 enqueue 不阻塞；agent 返回 `needs_human` 时 task 转 `waiting_human` 状态，worker 立即释放去拿下一个 task |
| 15 | DOCX patch 保留 named styles；三套 LaTeX 模板可从同一 IR 编译；cover-letter eval 5/5 通过；产物绑定 snapshot/source/template/trace ID |
| 16 | JobsView 任意被过滤的岗位 5 秒内看到 reason chain；100 个岗位 agent 成本 < $0.50 |
| 17 | 调度或手动触发 plan run → review queue 出现 N 条预生成 application，每条 30 秒内可 approve |
| 17.8 | 上传可信简历到文档库 → 设成默认材料来源 → 对 paused review entry 以该来源重新生成 → 再 promote 回文档库 |
| 17.9 | 对已有凭据的内置 provider 做 connect/test；Settings model picker 展示 curated/live catalog；`tier="small"` 的抽取调用走配置的小模型 provider |
| 18 | 已注册 worker stub 全部收口；异步材料/API 返回 `task_id` 且 `TaskRecord.result` 可读；worker 丢失后安全重入队；`dead_lettered` / 手动 retry 可用；每日自动 cleanup 把孤儿 artifact 移入 quarantine，scan / restore / purge 可用且 cleanup report 可审计 |
| 19 | 两个 tenant 设了重叠 email / LinkedIn cookie → 互相读不到对方的 job / snapshot / application / credential / Redis key（直 SQL + 直 Redis CLI 验证）；超配额返回 429 |

## 11. 风险与未决问题

- **LinkedIn 限流 / 检测。** 通过持久化 context cookie、随机延时、控并发、
  以及 Phase 13 由分布式锁把关的 force-refresh 来缓解。激进的批处理调度仍有实际
  风险。
- **LLM 成本漂移。** 通过 Phase 12 缓存、Phase 11 fallback 链、Phase 17.9 小模型层
  和 $1 / 100 case 的 eval 上限来缓解。成本遥测是早期预警。
- **Worker body 仍需激活。** Phase 14 已落地 Celery 骨架，但 Phase 18 才会把耗时
  搜索、生成、申请任务完整迁入真实 worker task，并补齐 retry / DLQ 行为。
- **任意 LaTeX 不是零配置。** Phase 15 接收任意模板，但必须先有
  manifest/adapter 且 sample compile 通过；全自动导入仍可能需要用户修正。
- **当下仍是单实例假设。** Phase 14 + D018/D023 铺了多实例工作；Phase 18 才真正
  做实。在此之前，**不要**对同一 Postgres / Redis 起两个 `autoapply web` 进程
  —— 数据层允许但没有 advisory lock，会引发重复提交。
- **Auto-submit 安全性。** `apply` 里有 `--auto-submit`，但仍走 HITL gate。
  我们还没看到能让我们按 vendor 摘掉 gate 的 eval 数据。
- **没有 SaaS 业务层。** Phase 21 是多租户托管基础设施，不是计费 / 注册 /
  营销。除非有商业 license 客户签约，否则这部分都在范围外。

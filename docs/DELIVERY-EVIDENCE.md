# 验收对照表（DELIVERY EVIDENCE）

工单 2067 / 【S5-EVAL-035】：提示词与模型配置的版本登记。
本表把**客户范围的每一条**对应到：实现文件与行号 → 覆盖它的测试 → 真实命令输出落盘。
所有输出文件都由 `scripts/capture_checks.py` / `scripts/capture_api_examples.py` 实际跑出来，可随时重跑复现。

环境：Python 3.11.9 / fastapi 0.136.1 / SQLAlchemy 2.0.49 / alembic 1.18.4 / pydantic 2.14.4 /
httpx 0.28.1 / PyYAML 6.0.2 / pytest 9.2.0；**未安装 psycopg，本机无 PostgreSQL 服务**，
因此全部证据在 SQLite 上产生（见 `docs/checks/py-version.txt`）。

---

## 范围第 1 条：`ai_prompt_version` 表

> 字段：tool、prompt_key、version 语义化版本号、content_sha256、file_path、changelog、created_by

| 项 | 位置 |
| --- | --- |
| ORM 模型 | `app/models.py`（`class AiPromptVersion`） |
| 迁移 | `migrations/versions/0001_prompt_registry.py:31`（`op.create_table("ai_prompt_version", …)`） |
| 统一列 | `app/models.py` 的 `OrgScoped` 混入：`org_id` / `site_id` / `created_at` / `created_by` / `updated_at` / `deleted_at` |
| 语义化版本落库 | `version` 原文 + `version_major` / `version_minor` / `version_patch` 三列整数 |
| 唯一性 | `UniqueConstraint("org_id","tool","prompt_key","version", name="uq_ai_prompt_version_scope")` |

验证：`tests/test_prompt_register.py::test_first_registration_inserts_one_row_per_file`（插入 5 行）；
`docs/checks/cli-ok.txt` 显示 `inserted 5, skipped 0, total 5`。

---

## 范围第 2 条：`ai_model_profile` 表（只存别名）

> 字段：name、endpoint_alias、params（temperature / max_tokens 等）；只存别名，实际地址映射到环境变量

| 项 | 位置 |
| --- | --- |
| ORM 模型 | `app/models.py`（`class AiModelProfile`），`params` 用 `JSONType = JSON().with_variant(JSONB(), "postgresql")` |
| 迁移 | `migrations/versions/0001_prompt_registry.py`（`sa.Column("params", postgresql.JSONB(…))`） |
| 别名→地址 | `app/config.py:alias_env_var` / `resolve_model_endpoint`：`model.main` → `MODEL_ALIAS_MODEL_MAIN` |
| 登记命令 | `app/cli_model_profile.py:56`（`main`），拒绝含 `/` 或 `:` 的别名（退出码 1） |
| 绑定返回 | `app/eval_binding.py:ModelProfileBinding.as_payload()` 只含 name / endpoint_alias / params |

验证：`tests/test_model_profile.py`（5 例：登记、二次更新同行、拒绝地址别名、参数越界、dry-run 不写）；
`tests/test_eval_binding.py::test_build_eval_context_returns_version_and_alias_only`、
`test_alias_resolves_only_through_the_environment`；
输出见 `docs/checks/api-examples.txt` 第 2 节（`address_from_env=MODEL_ALIAS_MODEL_MAIN`）。

---

## 范围第 3 条：`prompt-register` 命令

> 扫描 `prompts/`（每文件一个提示词，front-matter 含 tool/prompt_key/version），
> **内容哈希变化而版本号未提升时报错退出**，正常则登记入库；接在 CI 的 eval-lint 之前

| 项 | 位置 |
| --- | --- |
| 命令入口 | `app/cli_prompt_register.py:75`（`main`），参数 `--prompt-dir` / `--dry-run` / `--allow-missing-db` / `--created-by` |
| 文件解析 | `app/frontmatter.py:128`（`split_frontmatter`）、`load_prompt_file`、`discover_prompt_files` |
| 正文哈希 | `app/frontmatter.py:102`（`normalize_text`）、`:108`（`content_sha256`）——只算正文、CRLF→LF、首尾 strip |
| 版本比较 | `app/frontmatter.py:42`（`class Semver`），`self.tuple` 为 `(major, minor, patch)` 整数元组 |
| 规则计划 | `app/registry.py:272`（`plan_registration`）、`plan_actions`（skip / version_content_conflict / version_regression / duplicate_version） |
| 单事务写入 | `app/registry.py:302`（`apply_registration`）+ `app/cli_prompt_register.py` 单次 `session.commit()` |
| 退出码 | `EXIT_OK=0` / `EXIT_CONTENT=1` / `EXIT_OPERATIONAL=2`（`app/cli_prompt_register.py`） |
| CI 顺序 | `.github/workflows/ci.yml`：`migrate` → **`prompt-register`** → `eval-lint` → `pytest` |

验证（每条都有落盘的真实输出）：

| 规则 | 测试 | 输出文件 | 退出码 |
| --- | --- | --- | --- |
| 首次登记成功 | `test_first_registration_inserts_one_row_per_file` | `docs/checks/cli-ok.txt` | 0 |
| 幂等（不插新行） | `test_second_run_is_idempotent_and_reports_skipped` | `docs/checks/cli-idempotent.txt` | 0 |
| 改正文未升版本 | `test_content_change_without_version_bump_exits_1_and_writes_nothing` | `docs/checks/cli-fail-version.txt` | 1 |
| 版本回退 | `test_version_rollback_exits_1` | `docs/checks/cli-fail-rollback.txt` | 1 |
| 版本回退阻断整批 | `test_rollback_of_one_file_prevents_writing_the_whole_batch` | 同上（其余文件降级为 `[error] … not written`） | 1 |
| 同版本两文件 | `test_same_version_in_two_files_exits_1` | pytest 断言 | 1 |
| 缺字段 / 未知字段 / 版本两段 | `test_missing_frontmatter_field_exits_1`、`test_unknown_frontmatter_field_exits_1`、`test_version_with_two_segments_exits_1` | `docs/checks/cli-fail-frontmatter.txt`（`versoin`） | 1 |
| 改 changelog 不触发冲突 | `test_changing_changelog_only_keeps_the_hash_stable` | pytest 断言 | 0 |
| CRLF 与 LF 同哈希 | `test_crlf_and_lf_versions_of_the_same_body_hash_identically` | pytest 断言 | — |
| `--dry-run` 不写库 | `test_dry_run_reports_plan_without_writing` | pytest 断言 + `docs/checks/cli-dry-run-no-db.txt` | 0 |
| `--allow-missing-db` | `test_allow_missing_db_validates_files_without_a_database`、`test_allow_missing_db_still_reports_content_errors` | `docs/checks/cli-dry-run-no-db.txt` | 0 |
| 未配置库 / 未迁移 / 连不上 → 2 | `test_missing_database_configuration_exits_2`、`test_unmigrated_database_exits_2`、`test_unreachable_database_exits_2` | `docs/checks/cli-exit2-unmigrated.txt`、`cli-exit2-unreachable.txt` | 2 |

**退出码 1 与 2 的原始证据对比**（两条排查方向完全不同）：

```
# 内容违规 → 1（改提示词文件）
$ python -m app.cli_prompt_register --prompt-dir <改了正文的副本>
[fail] code=version_content_conflict file=answers/faq_answer.md version=1.1.0
       version 1.1.0 of member_service/faq_answer is already registered from
       answers/faq_answer.md with a different content hash
       (58a3ed4f3f57... vs 193ef74e0b25...); raise the version number instead of
       editing a registered version in place
[error] 1 content error(s); nothing was written
exit-code 1

# 库未迁移 → 2（跑迁移，别动提示词）
$ python -m app.cli_prompt_register --prompt-dir prompts
[error] database is reachable but not migrated (missing tables: ai_prompt_version,
        ai_model_profile, eval_run, eval_result); run alembic upgrade head first
exit-code 2
```

（完整原文见 `docs/checks/cli-fail-version.txt` 与 `docs/checks/cli-exit2-unmigrated.txt`。）

---

## 范围第 4 条：评测模式响应回显，执行器写入 eval_run / eval_result

> 被测服务在评测模式的响应中返回 prompt_version 与 model_profile，执行器写入 eval_run 与 eval_result

| 项 | 位置 |
| --- | --- |
| 绑定构造 | `app/eval_binding.py:138`（`build_eval_context`）→ `EvalBinding.as_payload()` |
| 地址延迟解析 | `EvalBinding.resolve_endpoint()` → `app/config.py:resolve_model_endpoint`（只在真正调模型时查环境变量） |
| 执行器写入 | `app/eval_binding.py:179`（`record_eval_run`），一次写入 `eval_run` + N 条 `eval_result`，`total_cases`/`passed_cases` 由 `outcomes` 推导 |
| 结果表外键 | `eval_result.prompt_version_id` → `ai_prompt_version.id`（`ondelete=RESTRICT`），`run_id` → `eval_run.id`（`CASCADE`） |

验证：`tests/test_eval_binding.py`（5 例）、`tests/test_scenario_demo.py`（端到端 1.0.0 → 1.1.0 → 两次评测 → 查接口）；
真实报文见 `docs/checks/api-examples.txt` 第 2 节（服务的回显）与第 3 节（执行后接口的读出）。

---

## 范围第 5 条：`GET /api/eval/prompt-versions/{tool}/{prompt_key}/history`

> 返回每个版本最近一次评测的通过率与运行 id

| 项 | 位置 |
| --- | --- |
| 路由 | `app/api_eval.py:161`（`prompt_version_history`），prefix `/api/eval` |
| 响应模型 | `app/schemas.py:PromptVersionHistory` / `PromptVersionHistoryItem`（`extra="forbid"`） |
| 最近一次运行的判定 | `app/api_eval.py:87`（`_latest_evaluation_per_version`），排序键 `(finished_at or started_at or created_at, run_id)` |
| 通过率分母为 0 | `rate = (passed / total) if total > 0 else None` |
| 排序 | 按 `Semver.tuple` 升序（`_version_tuple`），非字符串序 |
| org 隔离 | `org_id` 过滤；跨 org 与不存在同返回 404 |

验证：`tests/test_api_eval.py` 中 `test_history_returns_one_row_per_version_with_pass_rates`、
`test_history_uses_the_most_recent_run_not_the_first`、`test_history_returns_null_pass_rate_when_never_evaluated`、
`test_history_returns_null_pass_rate_when_denominator_is_zero`、`test_history_is_404_across_orgs`、
`test_history_is_404_for_unknown_prompt`、`test_history_rejects_malformed_tool_path`。
真实响应（`docs/checks/api-examples.txt`）：`1.0.0` → `0.5`、`1.1.0` → `1.0`、`1.2.0` → `null`。

---

## 范围第 6 条：`GET /api/eval/runs/{id}/versions`

> 返回某次运行涉及的全部提示词版本，用于定位退化由哪个版本引入

| 项 | 位置 |
| --- | --- |
| 路由 | `app/api_eval.py:244`（`run_versions`） |
| 响应模型 | `app/schemas.py:RunVersions` / `RunVersionItem`（tool、prompt_key、version、content_sha256） |
| 口径 | `join eval_result` + `distinct`，只返回该运行**实际引用**的版本，不是库里全部版本 |
| 排序 | `(tool, prompt_key, Semver.tuple)` |
| 404 | 运行不存在或属于其它 org |

验证：`tests/test_api_eval.py::test_run_versions_returns_only_the_versions_the_run_used`、
`test_run_versions_sorted_by_tool_then_prompt_key`、`test_run_versions_is_404_for_unknown_run`、
`test_run_versions_is_404_across_orgs`。
真实响应：某次运行只用了 `member_service/faq_answer@1.1.0` 一版（即使库里已有 1.0.0/1.1.0/1.2.0 三版），
跨 org 访问同 id 返回 404。

---

## 客户统一技术约定逐条核对

| 约定 | 落实方式 | 机器校验 |
| --- | --- | --- |
| Python 3.11+ / FastAPI / SQLAlchemy 2 / Alembic | `pyproject.toml` `requires-python = ">=3.11"`；`app/main.py`、`app/db.py`、`migrations/` | `docs/checks/py-version.txt` |
| PostgreSQL 16（pgvector） | 迁移用 `postgresql.JSONB`、`sa.Uuid`；CI 服务容器 `pgvector/pgvector:pg16` | `make migrate-sql` 可离线核对 DDL |
| 业务表统一含 id/org_id/site_id/created_at/created_by/updated_at/deleted_at | `app/models.py` 的 `OrgScoped` 混入，四表全部继承 | 迁移文件里四表列一致 |
| 金额一律以分存整数 | 本单无金额字段；`params` 里只有 `temperature`/`max_tokens`/`top_p`，无价格 | — |
| 配置与密钥只从环境变量读 | `app/config.py` 全量 `os.environ`；无默认地址 | `test_dependency_rules.py::test_config_reads_configuration_from_environment_only` |
| 代码里不得出现密钥/连接串/真实地址 | 源码零 `http://`/`https://` 字面量 | `test_no_hard_coded_network_addresses`、`test_no_credentials_or_dsns_literals` |
| 不得引入向境外发送数据的第三方 SDK | 依赖表只有 fastapi/uvicorn/SQLAlchemy/alembic/pydantic/PyYAML | `test_no_third_party_egress_sdk_is_imported`（ast 扫描 import） |
| 只用虚构测试数据 | 全部 `example.invalid` / 全零或 `1111…`/`2222…` UUID / 虚构会员业务话术 | 源码与测试内无真实主体 |
| 交付物不出现客户名称 | 全仓无客户名，业务域写作 “会员空间” 一类通用描述 | 人工核对 |

---

## 证据文件索引

| 文件 | 内容 |
| --- | --- |
| `docs/checks/pytest.txt` | `python -m pytest -q` 全量输出，`54 passed`，退出码 0 |
| `docs/checks/py-version.txt` | `python -V`（3.11.9）与关键包版本、`psycopg installed: no` |
| `docs/checks/db-init.txt` | SQLite 建表（生产等价物 `alembic upgrade head`） |
| `docs/checks/cli-ok.txt` | 首次登记 5 行，退出码 0 |
| `docs/checks/cli-idempotent.txt` | 第二次运行 5 个 `[skip]`，`inserted 0`，退出码 0 |
| `docs/checks/cli-fail-version.txt` | 改正文未升版本，`code=version_content_conflict`，退出码 1 |
| `docs/checks/cli-fail-rollback.txt` | 版本回退 1.0.9，`code=version_regression`，退出码 1 |
| `docs/checks/cli-fail-frontmatter.txt` | `versoin` 拼错，`code=unknown_field` + `did you mean 'version'`，退出码 1 |
| `docs/checks/cli-dry-run-no-db.txt` | 无库 + `--dry-run --allow-missing-db`，退出码 0 |
| `docs/checks/cli-exit2-unmigrated.txt` | 库未迁移，退出码 2 |
| `docs/checks/cli-exit2-unreachable.txt` | 库连不上（缺 psycopg 驱动），退出码 2 |
| `docs/checks/api-examples.txt` | 完整登记流程 + 评测写入 + 四个真实 HTTP 报文 |

重跑方式：

```bash
python scripts/capture_checks.py         # 校验每个命令的退出码是否符合预期
python scripts/capture_api_examples.py   # 重新生成 API 报文
```

---

# 补充证据 v2（针对 delivery_check 的三项 unclear）

第一轮交付（commit `c607428`）之后，平台的 `delivery_check` 六镜头中 relevance、substance 为 matched，
feasibility / acceptance / evidence 三项为 unclear，建议原文分别是：

> feasibility: Specify dependencies, access requirements and a feasible implementation path.
> acceptance: Address every customer acceptance criterion, including constraints and edge cases.
> evidence: Provide reproducible verification steps and the evidence required by the task.

本节逐条补齐这三项，**只新增文档与证据，不改动任何业务代码、测试断言或既有证据文件**。
下面每一条数字都来自本轮在干净 clone 上真实跑出的输出，落盘在 `docs/checks/v2-*.txt`，
并可用一条命令全部重跑：

```bash
git clone https://github.com/liaoyuqing/clawhunt-2067-prompt-registry.git
cd clawhunt-2067-prompt-registry
python scripts/capture_checks_v2.py     # 重写全部 10 个 v2-*.txt 并自检退出码
# 末行输出 all captured commands matched their expectations，进程退出码 0
```

该脚本对每个被捕获的命令都断言了预期退出码与关键输出（不符则脚本自身退出码 1，并列出 MISMATCHES），
所以“证据文件存在”与“证据文件仍成立”是同一件事。

---

## A. 依赖与运行环境（对应 feasibility）

### A.1 需要什么

| 组件 | 版本（本轮实测） | 用途 | 证据 |
| --- | --- | --- | --- |
| Python | 3.11.9 | 解释器，`pyproject.toml` 要求 `>=3.11` | `docs/checks/v2-environment.txt` |
| FastAPI | 0.136.1 | 两个查询接口 | 同上 |
| Uvicorn | 0.47.0 | 起服务（抓 HTTP 报文时用） | 同上 |
| SQLAlchemy | 2.0.49 | ORM 与 session | 同上 |
| Alembic | 1.18.4 | 迁移（生产 PostgreSQL 路径） | `docs/checks/v2-install.txt` |
| Pydantic | 2.14.4 | 响应模型（`extra="forbid"`） | `docs/checks/v2-environment.txt` |
| PyYAML | 6.0.2 | 解析提示词 front-matter | 同上 |
| pytest | 9.2.0 | 54 个用例 | `docs/checks/v2-pytest.txt` |
| httpx | 0.28.1 | 接口测试的 TestClient 传输层 | `docs/checks/v2-environment.txt` |

安装：`python -m pip install -r requirements-dev.txt`（一条命令包含运行 + 测试依赖）。
三个 requirements 文件的分工写死在注释里：`requirements.txt` 是运行时（**不含数据库驱动**），
`requirements-dev.txt` 追加 pytest 与 httpx，`requirements-postgres.txt` 才追加 `psycopg[binary]`。

### A.2 不需要什么（这是本单可复现性的关键）

| 项 | 本轮实测结论 | 证据 |
| --- | --- | --- |
| PostgreSQL 服务 | 不需要：54 个用例 + 全部 CLI 链路都在 SQLite 上跑通。`import psycopg` 失败（`ModuleNotFoundError`），127.0.0.1:5432 无监听 | `docs/checks/v2-environment.txt` |
| 数据库驱动 | 不需要：`requirements.txt` 刻意不含 psycopg；`app/db.py` 只在真要建连接时才 import 驱动 | `docs/checks/v2-install.txt` |
| 任何环境变量 | 不需要：把 `DATABASE_URL` / `PROMPT_CREATED_BY` 置空后 `python -m pytest -q` 仍 `54 passed` | `docs/checks/v2-environment.txt` |
| API key / token / 外部服务 | 不需要：仓库内零地址字面量，`test_no_hard_coded_network_addresses` 静态扫描锁死 | `tests/test_dependency_rules.py`（5 例） |
| 网络访问 | 不需要：整条链路只碰本地 SQLite 与 127.0.0.1 的 uvicorn | 同上 |

### A.3 从零到跑起来（逐条命令 + 每步应看到什么）

```bash
# 1) 取代码
git clone https://github.com/liaoyuqing/clawhunt-2067-prompt-registry.git
cd clawhunt-2067-prompt-registry

# 2) 装依赖（无数据库驱动）
python -m pip install -r requirements-dev.txt
# → Requirement already satisfied: … (28 行)，exit 0

# 3) 全量测试（不需要任何环境变量）
python -m pytest -q
# → 54 passed，exit 0（耗时随机器负载浮动，不构成断言；断言的是 54 passed 与退出码 0）

# 4) 建表。本机/CI 的 SQLite 等价物（直接用仓库根目录下的文件也行，例如 dev.sqlite3）：
mkdir -p build/v2/db          # SQLite 不会自建父目录，缺了会报 unable to open database file
DATABASE_URL=sqlite:///./build/v2/db/run.sqlite3 PROMPT_CREATED_BY=platform-ops@example.invalid \
  python -m app.dbcheck init
# → schema ready in sqlite:///./build/v2/db/run.sqlite3，exit 0

# 5) 登记提示词（首次 / 再跑一次，验证幂等）
python -m app.cli_prompt_register --prompt-dir prompts
# 首次 → inserted 5, skipped 0, total 5，exit 0
python -m app.cli_prompt_register --prompt-dir prompts
# 再次 → inserted 0, skipped 5, total 5，exit 0

# 6) 登记模型采样配置（只存别名）
python -m app.cli_model_profile --name model.main --endpoint-alias model.main \
  --temperature 0.2 --max-tokens 1024
# → model-profile: insert name=model.main alias=model.main params={"max_tokens": 1024, "temperature": 0.2}
#    address_from_env=MODEL_ALIAS_MODEL_MAIN，exit 0

# 7) 造三趟同版本（1.1.0）的评测运行，通过率 0.6 / 0.9 / 0.8
python scripts/replay_history.py build/v2/db/run.sqlite3
# → seeded run … rate=0.6000 / 0.9000 / 0.8000，exit 0

# 8) 起服务抓两个接口的报文
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 另开一个终端：
curl -s http://127.0.0.1:8000/api/eval/prompt-versions/member_service/faq_answer/history
curl -s http://127.0.0.1:8000/api/eval/runs/<上一步的 latest_run_id>/versions
```

> 已实测的一处坑：SQLite 不会自动创建父目录，`DATABASE_URL=sqlite:///./不存在的目录/x.sqlite3` 会直接
> `sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unable to open database file`（exit 1）；
> 先 `mkdir -p` 该目录即可，`docs/checks/v2-install.txt` 与 `v2-ac2-idempotent.txt` 里的路径都是已存在的目录。
> 用仓库根目录下的 `dev.sqlite3` 则无此问题（`README.md` 的快速开始用的就是这个）。

生产路径（本机无法实跑，如实标注）：`pip install -r requirements-postgres.txt` →
`DATABASE_URL=postgresql+psycopg://… python -m alembic upgrade head`。本机执行这条命令得到的是
`DatabaseUnavailable: database driver for 'postgresql+psycopg' is not installed …`（exit 1），
这正是“驱动可选”的设计在报错里点名了两条出路；迁移要落的 DDL 用离线模式渲染出来了：
`python -m alembic upgrade head --sql` → 83 行非空 DDL（含 `ai_prompt_version` 的 15 列与 `ai_model_profile` 的 10 列），exit 0。
CI（`pgvector/pgvector:pg16` 服务容器）里跑的是同一条 `python -m alembic upgrade head`，见 `.github/workflows/ci.yml`。

### A.4 可实施的落地路径

1. **接入方式**：被测服务在评测模式下调 `app.eval_binding.build_eval_context(...)`，把 `prompt_version` 与
   `model_profile` 放进响应（只回别名，见 AC4）；执行器调 `record_eval_run(...)` 把结果写进 `eval_run` / `eval_result`，
   事务由调用方掌握。两边都不需要改数据库结构。
2. **CI 顺序**：`.github/workflows/ci.yml` 是 `migrate` → `prompt-register` → `eval-lint` → `pytest`。
   登记必须先于 lint，因为 lint 拿的是已登记版本的哈希；这一步失败说明提示词文件本身有问题，不该进入评分环节。
3. **上线顺序**：先 `alembic upgrade head`（幂等，重复执行无副作用），再 `prompt-register`（幂等，同内容重跑只会 `[skip]`），
   最后切流量；回滚只需把 `MODEL_ALIAS_*` 指回旧地址，数据不用动。
4. **权限要求**：只需要一个能建表与读写四张表的库账号；无需对象存储、消息队列、Redis（本单范围内没有缓存/队列需求）。

---

## B. 逐条复现步骤（对应 acceptance + evidence）

> 每一行都是「① 执行什么 → ② 应看到什么（含退出码）→ ③ 证据文件」。
> 证据文件里的每段输出前面都有产生它的那条命令，可直接对照重跑。

### B.1 AC1 —— 改了正文但没升版本号，必须非零退出且一行都不写

| | 内容 |
| --- | --- |
| ① 复现命令 | 复制 `prompts/` 为 `build/v2/ac1_prompts/`，只用 Python 追加一行正文（`io.open(..., encoding="utf-8", newline="\n")`，因为 Windows 控制台默认 cp936 会把中文正文写坏），保持 `version: 1.1.0` 不变；然后 `python -m app.cli_prompt_register --prompt-dir build/v2/ac1_prompts` |
| ② 应看到 | `[error] code=version_content_conflict file=answers/faq_answer.md version=1.1.0 …`；`[fail] code=version_content_conflict …`；`[error] 1 content error(s); nothing was written`；`exit-code 1`；进程退出码 **1**；`ai_prompt_version` 行数 **5 → 5** |
| ③ 证据 | `docs/checks/v2-ac1-conflict.txt`（另：`docs/checks/cli-fail-version.txt`、`cli-fail-rollback.txt` 为第一轮同名场景） |
| 实现位置 | `app/registry.py:204-217`（`RegistryError("version_content_conflict", …)`）、`app/cli_prompt_register.py:136-143`（`session.rollback()` + `EXIT_CONTENT=1`）、整批只在 `app/cli_prompt_register.py:152-156` 提交一次 |
| 测试 | `tests/test_prompt_register.py::test_content_change_without_version_bump_exits_1_and_writes_nothing`、`::test_rollback_of_one_file_prevents_writing_the_whole_batch` |

对照组（同一处改动、但把 version 提到 1.2.0）必须被接受：

| | 内容 |
| --- | --- |
| ① 命令 | 同一份 `build/v2/ac1_prompts/`，只把 `version: 1.1.0` 改成 `1.2.0`：`python -m app.cli_prompt_register --prompt-dir build/v2/ac1_prompts` |
| ② 应看到 | `[insert] tool=member_service prompt_key=faq_answer version=1.2.0 sha256=0b20573dd751 …`；`inserted 1, skipped 4, total 5`；`exit-code 0`；行数 **5 → 6** |
| ③ 证据 | `docs/checks/v2-ac1-bumped-ok.txt` |

### B.2 AC2 —— 同一内容重复登记不得产生第二行

| | 内容 |
| --- | --- |
| ① 命令 | 连续两次 `python -m app.cli_prompt_register --prompt-dir prompts` |
| ② 应看到 | 第一次 `inserted 5, skipped 0, total 5`（行数 0 → 5，exit 0）；第二次 5 条 `[skip] … (already registered with identical content)`、`inserted 0, skipped 5, total 5`（行数 5 → 5，exit 0） |
| ③ 证据 | `docs/checks/v2-ac2-idempotent.txt`（另：`docs/checks/cli-ok.txt`、`cli-idempotent.txt`） |
| 实现位置 | `app/registry.py:188-203`（哈希相等即 `skip`）、`app/models.py:65-70`（`uq_ai_prompt_version_scope` 唯一约束兜底）、`app/frontmatter.py:102-110`（`normalize_text` + `content_sha256`，只算正文、CRLF→LF、首尾 strip） |
| 测试 | `tests/test_prompt_register.py::test_second_run_is_idempotent_and_reports_skipped`、`::test_crlf_and_lf_versions_of_the_same_body_hash_identically`、`tests/test_frontmatter.py::test_hash_ignores_frontmatter_and_trailing_whitespace` |

边界（都已有用例）：改 `changelog` 不算内容变更（`test_changing_changelog_only_keeps_the_hash_stable`）；
CRLF 与 LF 同哈希；版本按整数三元组比较，所以 `1.10.0 > 1.9.0`（`test_semver_compares_numerically_not_lexicographically`）。

### B.3 AC3 —— history 返回每个版本**最近一次**评测的通过率

| | 内容 |
| --- | --- |
| ① 命令 | `python scripts/replay_history.py build/v2/db/run.sqlite3`（造 0.6 / 0.9 / 0.8 三趟，同一版本 1.1.0，`finished_at` 依次递增）→ 起 `uvicorn` → `curl -s http://127.0.0.1:<port>/api/eval/prompt-versions/member_service/faq_answer/history` |
| ② 应看到 | HTTP 200；`1.1.0` 的 `latest_pass_rate` = **0.8**（第三趟；既不是第一趟 0.6，也不是均值 0.7667）；未评测过的 `1.2.0` 的 `latest_pass_rate` = **null**（不是 0.0）；`latest_run_id` 等于**第三趟**（0.8）那次的 run id，也就是 `replay_history.py` 打印的最后一行 |
| ③ 证据 | `docs/checks/v2-ac3-history-runs.txt`（三趟的库内真值）、`docs/checks/v2-ac3-api-responses.txt`（真实 JSON 报文）、`docs/checks/api-examples.txt` |
| 实现位置 | 路由 `app/api_eval.py:152-166`；取“最近一次” `app/api_eval.py:87-96`（排序键 `finished_at` → `started_at` → `created_at` → run id，绝不按插入顺序）；`app/api_eval.py:210` `rate = (passed / total) if total > 0 else None`；响应契约 `app/schemas.py:11-39`（`extra="forbid"`，注释里写死“never 0.0 as a stand-in for 'not evaluated'”） |
| 测试 | `tests/test_api_eval.py::test_history_uses_the_most_recent_run_not_the_first`、`::test_history_returns_one_row_per_version_with_pass_rates`、`::test_history_returns_null_pass_rate_when_never_evaluated`、`::test_history_returns_null_pass_rate_when_denominator_is_zero`、`::test_history_is_404_across_orgs` |

同一个运行 id 的另一面 `GET /api/eval/runs/{id}/versions` 必须**只**返回该次运行实际用过的版本：

| | 内容 |
| --- | --- |
| ① 命令 | `curl -s http://127.0.0.1:<port>/api/eval/runs/<history 返回的 latest_run_id>/versions` |
| ② 应看到 | HTTP 200；`versions` 只有 1 条（`member_service/faq_answer@1.1.0`），而此刻库里已登记 **6** 个版本；未知 run id → HTTP 404；换 `X-Org-Id: 2222…` 看同一 id → HTTP 404（不是 403：403 会确认这个 id 存在） |
| ③ 证据 | `docs/checks/v2-ac3-api-responses.txt` 第 2–5 节 |
| 实现位置 | `app/api_eval.py`（`run_versions`，`join eval_result` + `distinct`）；响应模型 `app/schemas.py` 的 `RunVersionItem` |
| 测试 | `tests/test_api_eval.py::test_run_versions_returns_only_the_versions_the_run_used`、`::test_run_versions_sorted_by_tool_then_prompt_key`、`::test_run_versions_is_404_for_unknown_run`、`::test_run_versions_is_404_across_orgs` |

### B.4 AC4 —— 库里只存别名，真实地址只来自环境变量

| | 内容 |
| --- | --- |
| ① 命令 | `python -m app.cli_model_profile --name model.main --endpoint-alias model.main --temperature 0.2 --max-tokens 1024`；随后逐表逐行扫描 `http://` / `https://` 字面量；再在环境变量未设置时 `python -c "from app import config; print(config.resolve_model_endpoint('model.main'))"` |
| ② 应看到 | 登记成功、`params={"max_tokens": 1024, "temperature": 0.2}`、`address_from_env=MODEL_ALIAS_MODEL_MAIN`、exit 0；`ai_model_profile` 列为 `id,name,endpoint_alias,params,org_id,site_id,created_at,created_by,updated_at,deleted_at`（**没有** url / base_url 列）；四张表全表扫描 `http literal hits: 0`；别名映射出 `MODEL_ALIAS_MODEL_MAIN`；未设置时抛 `ConfigError: model endpoint alias 'model.main' has no target configured: set MODEL_ALIAS_MODEL_MAIN …`，exit **1** |
| ③ 证据 | `docs/checks/v2-ac4-alias-only.txt`（另：`docs/checks/api-examples.txt`、`py-version.txt`） |
| 实现位置 | `app/models.py:73-85`（`AiModelProfile`，注释 “Only an alias is stored, never an address.”）、`migrations/versions/0001_prompt_registry.py:65-83`（建表列）、`app/config.py:104-111`（`alias_env_var`）、`app/config.py:114-124`（`resolve_model_endpoint`，无默认值）、`app/eval_binding.py:74-79`（`as_payload` 只含 name / endpoint_alias / params） |
| 测试 | `tests/test_model_profile.py::test_model_profile_rejects_an_address_as_alias`、`tests/test_dependency_rules.py::test_no_hard_coded_network_addresses`、`::test_config_reads_configuration_from_environment_only`、`tests/test_eval_binding.py::test_alias_resolves_only_through_the_environment`、`::test_build_eval_context_returns_version_and_alias_only` |

---

## C. 实测数字（全部为本轮真实跑出）

### C.1 测试

原文见 `docs/checks/v2-pytest.txt`，逐字摘录：

```text
$ python -m pytest -q
......................................................                   [100%]
54 passed in 1.85s

[exit code: 0]
```

最后一行是 pytest 自己报的墙钟时间，每次重跑都会变（同一台机器上实测在 1.1s ~ 3.7s 之间），
**不构成断言**；断言的是 `54 passed` 与退出码 0。上面这段与本仓库此刻的
`docs/checks/v2-pytest.txt` 完全一致。

`python -m pytest --collect-only -q` 的分布（7 个文件 / 54 个用例）：

| 文件 | 用例数 |
| --- | --- |
| `tests/test_prompt_register.py` | 17 |
| `tests/test_api_eval.py` | 11 |
| `tests/test_frontmatter.py` | 10 |
| `tests/test_dependency_rules.py` | 5 |
| `tests/test_eval_binding.py` | 5 |
| `tests/test_model_profile.py` | 5 |
| `tests/test_scenario_demo.py` | 1 |
| **合计** | **54** |

证据：`docs/checks/v2-pytest.txt`、`docs/checks/v2-test-inventory.txt`。
把 `DATABASE_URL` 与 `PROMPT_CREATED_BY` 置空后仍是 `54 passed`（`docs/checks/v2-environment.txt`），
即用例不依赖任何本机配置。

### C.2 命令退出码与行数变化

| 命令 | 关键输出 | 退出码 | 行数 |
| --- | --- | --- | --- |
| `python -m app.dbcheck init` | `schema ready in sqlite:///./build/v2/db/run.sqlite3` | 0 | 0 → 0（建表） |
| `prompt-register`（首次） | `inserted 5, skipped 0, total 5` | 0 | 0 → 5 |
| `prompt-register`（再次） | `inserted 0, skipped 5, total 5` | 0 | 5 → 5 |
| `prompt-register`（改正文未升版本） | `code=version_content_conflict`（`58a3ed4f3f57…` vs `0d5d42737843…`）、`1 content error(s); nothing was written` | **1** | 5 → 5 |
| `prompt-register`（同一改动升到 1.2.0） | `[insert] … version=1.2.0 sha256=0b20573dd751`、`inserted 1, skipped 4, total 5` | 0 | 5 → 6 |
| `cli_model_profile` | `alias=model.main params={"max_tokens": 1024, "temperature": 0.2} address_from_env=MODEL_ALIAS_MODEL_MAIN` | 0 | `ai_model_profile` 1 行 |
| `scripts/replay_history.py` | `rate=0.6000` / `0.9000` / `0.8000` | 0 | `eval_run` 3 行 |
| `alembic upgrade head`（未设 `DATABASE_URL`） | `RuntimeError: DATABASE_URL is not set; alembic will not guess a database.` | **1** | — |
| `alembic upgrade head`（指向 PostgreSQL，本机无驱动） | `DatabaseUnavailable: database driver for 'postgresql+psycopg' is not installed …` | **1** | — |
| `alembic upgrade head --sql`（离线） | 83 行非空 DDL | 0 | — |
| `alembic upgrade head`（SQLite） | `Running upgrade  -> 0001_prompt_registry` | 0 | 四表 |

### C.3 接口实测值

| 观测点 | 值 |
| --- | --- |
| `1.1.0` 的 `latest_pass_rate` | **0.8**（第三趟 / 最近一次） |
| 若取第一趟会是 | 0.6（**不是**这个） |
| 若取均值会是 | 0.7667（**不是**这个） |
| 未评测版本 `1.2.0` 的 `latest_pass_rate` | **null**（不是 0.0） |
| `GET /api/eval/runs/{id}/versions` 返回条数 | 1（库里已有 6 个版本） |
| 未知 run id | HTTP 404 |
| 跨 org 访问同一 run id | HTTP 404 |
| 跨 org 访问同一 history | HTTP 404 |
| `ai_model_profile` 列 | `id,name,endpoint_alias,params,org_id,site_id,created_at,created_by,updated_at,deleted_at` |
| 四张表内 `http://` / `https://` 字面量命中数 | **0** |

### C.4 环境边界（如实声明）

- 上述全部数字在 **SQLite** 上产生（`DATABASE_URL=sqlite:///./build/v2/db/run.sqlite3`）。
- 本机没有 PostgreSQL 服务、也没有 `psycopg`，因此 **没有** 在真实 PostgreSQL 上实跑迁移与 JSONB 落盘；
  这三条路径（迁移、JSONB、驱动装载）由 CI 的 `pgvector/pgvector:pg16` 服务容器覆盖，本机只渲染了离线 DDL 作为可核对物。
- 未接 Redis、未接统一鉴权中间件（`org_id` 目前取请求头 / 环境变量），与第一轮 `docs/DELIVERY-EVIDENCE.md` 第 9 节“已知边界”一致，本轮无变化。

---

## D. 证据文件索引（v2 追加）

| 文件 | 内容 |
| --- | --- |
| `docs/checks/v2-environment.txt` | 解释器与 8 个关键包版本、`import psycopg` 失败、5432 无监听、清空环境变量后仍 `54 passed` |
| `docs/checks/v2-install.txt` | 三个 requirements 文件原文、`pip install` 结果、alembic 四条路径（未设 URL / PostgreSQL 无驱动 / 离线 DDL / SQLite 实跑） |
| `docs/checks/v2-pytest.txt` | `python -m pytest -q` 全量输出 |
| `docs/checks/v2-test-inventory.txt` | 7 个测试文件、54 个用例的分布 |
| `docs/checks/v2-ac2-idempotent.txt` | 首次登记 5 行 + 再跑全部 `[skip]`，行数 0→5→5 |
| `docs/checks/v2-ac1-conflict.txt` | 改正文未升版本：`code=version_content_conflict`，退出码 1，行数 5→5 |
| `docs/checks/v2-ac1-bumped-ok.txt` | 同一改动升到 1.2.0：插入 1 行，行数 5→6，退出码 0 |
| `docs/checks/v2-ac3-history-runs.txt` | 三趟运行的库内真值（0.6000 / 0.9000 / 0.8000） |
| `docs/checks/v2-ac3-api-responses.txt` | 两个接口的完整 HTTP 报文，含两个 404 |
| `docs/checks/v2-ac4-alias-only.txt` | 列名、只存别名、全库零地址字面量、别名解析与失败 |
| `scripts/capture_checks_v2.py` | 一键重跑上面 10 个文件，并对每个命令断言退出码（不符则脚本退出 1） |
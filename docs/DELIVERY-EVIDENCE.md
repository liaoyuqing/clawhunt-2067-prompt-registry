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

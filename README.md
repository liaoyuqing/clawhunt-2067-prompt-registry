# 提示词与模型配置版本登记（ai-eval-registry）

给评测体系补上**可追溯的版本底座**：每一版提示词、每一个模型采样配置都登记入库并带内容哈希，
被测服务在评测模式下回显自己用的是哪一版，执行器把结果写进 `eval_run` / `eval_result`，
于是任何一个通过率都能追到“哪一版提示词、哪一个模型配置、哪一次运行”。

- 后端：Python 3.11 / FastAPI / SQLAlchemy 2 / Alembic / PostgreSQL 16（生产）/ SQLite（本机与测试）
- 全部配置只从环境变量读取，仓库内**没有任何**地址、连接串、密钥字面量（由 `tests/test_dependency_rules.py` 静态扫描锁死）
- 全部测试数据为虚构内容，不含任何真实企业或个人信息

> 验收核对表：[ACCEPTANCE-EVIDENCE.md](ACCEPTANCE-EVIDENCE.md) —— 一条验收标准一行，给出实现位置、pytest 节点 id、可原样执行的命令、断言的具体值与证据文件。

---

## 1. 快速开始（三条命令）

```bash
make setup          # 安装运行与测试依赖（不含数据库驱动，测试用 SQLite）
make test           # python -m pytest -q  → 54 passed
```

没有 PostgreSQL 时的完整本地链路（SQLite 建表 + 登记 + 起服务）：

```bash
export DATABASE_URL=sqlite:///./dev.sqlite3
export PROMPT_CREATED_BY=platform-ops@example.invalid

make migrate-sqlite      # = python -m app.dbcheck init（等价于生产上的 alembic upgrade head）
make prompt-register     # 扫描 prompts/ 并登记，幂等
make model-profile       # 登记别名 model.main（只存别名，不存地址）
make run                 # uvicorn app.main:app，默认 127.0.0.1:8000
```

生产（PostgreSQL 16）路径：

```bash
pip install -r requirements-postgres.txt      # 唯一需要 psycopg 的地方
export DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<db>
make migrate                                  # python -m alembic upgrade head
make prompt-register
```

### 1.1 Make 目标一览

| 目标 | 实际命令 | 用途 |
| --- | --- | --- |
| `make setup` | `pip install -r requirements-dev.txt` | 装依赖（SQLite 全部测试够用） |
| `make setup-postgres` | `pip install -r requirements-postgres.txt` | 生产额外需要 psycopg |
| `make migrate` | `python -m alembic upgrade head` | 生产迁移（PostgreSQL 方言） |
| `make migrate-check` | `python -m alembic current` | 查看当前 revision |
| `make migrate-sql` | `python -m alembic upgrade head --sql` | 离线打印 DDL，不连库 |
| `make migrate-sqlite` | `python -m app.dbcheck init` | 本机用 SQLite 建同样的表 |
| `make prompt-register` | `python -m app.cli_prompt_register --prompt-dir prompts` | 登记提示词（CI 里在 eval-lint 之前跑） |
| `make prompt-register-dry` | 同上加 `--dry-run` | 只打印计划 |
| `make model-profile` | `python -m app.cli_model_profile ...` | 登记模型采样配置 |
| `make test` | `python -m pytest -q` | 全量测试 |
| `make run` | `python -m uvicorn app.main:app` | 起服务 |
| `make checks` | `python scripts/capture_checks.py` | 重新生成 `docs/checks/*.txt` |

> 直接跑命令也完全等价，Make 只是薄封装。

---

## 2. 环境变量

**所有配置都只从环境变量读**；缺失时给出指名变量的明确报错，绝不回落到代码里的默认地址。

| 变量 | 必填 | 示例 | 说明 |
| --- | --- | --- | --- |
| `DATABASE_URL` | 生产必填 | `postgresql+psycopg://user:pass@host:5432/db` | 应用库。测试与 `--allow-missing-db` 场景可不设。 |
| `PROMPT_CREATED_BY` | 是 | `platform-ops@example.invalid` | 写入 `created_by` 的审计身份，缺失则退出码 2。 |
| `EVAL_ORG_ID` | 否 | `11111111-1111-1111-1111-111111111111` | 租户隔离。不设时用全零 UUID，而且所有命令都会把它打印出来（不静默）。 |
| `EVAL_SITE_ID` | 否 | `33333333-3333-3333-3333-333333333333` | 站点维度，不设时全零 UUID。 |
| `EVAL_MODEL_PROFILE` | 否 | `default` | 评测默认使用的 model profile 名。 |
| `MODEL_ALIAS_<ALIAS>` | 否 | `MODEL_ALIAS_MODEL_MAIN=<部署环境提供> ` | **唯一的地址存放处**。别名 `model.main` → `MODEL_ALIAS_MODEL_MAIN`（非字母数字转下划线并大写）。本文件不写示例地址。 |
| `API_HOST` / `API_PORT` | 否 | `127.0.0.1` / `8000` | `make run` 的监听地址。 |

真实地址**只允许**出现在部署环境的 `MODEL_ALIAS_*` 里。公共仓库里出现 `http://` 或 `https://` 字面量会被
`tests/test_dependency_rules.py::test_no_hard_coded_network_addresses` 直接判红（见第 6 节）。

---

## 3. 目录结构

```
app/
  config.py               只读环境变量：DATABASE_URL、PROMPT_CREATED_BY、MODEL_ALIAS_* …
  db.py                   SQLAlchemy 2 engine/session（sqlite 与 postgresql 都走同一套）
  dbcheck.py              连库与“是否已迁移”检查；不连库时给退出码 2 的明确原因
  models.py               ai_prompt_version / ai_model_profile / eval_run / eval_result
  schemas.py              pydantic v2 响应模型
  frontmatter.py          --- 包裹的 YAML 头解析、白名单校验、正文哈希、语义化版本
  registry.py             登记核心：校验规则、幂等判断、批量计划、单事务写入
  cli_prompt_register.py  prompt-register 命令（退出码 0/1/2）
  cli_model_profile.py    model-profile 命令（只登记别名）
  eval_binding.py         评测模式的绑定：返回 prompt_version + model_profile；写 eval_run/eval_result
  api_eval.py             两个 GET 接口（org 维度隔离）
  main.py                 FastAPI 装配 + /healthz
migrations/
  versions/0001_prompt_registry.py   PostgreSQL 16 方言的四表迁移
prompts/
  answers/faq_answer.md           member_service/faq_answer   1.1.0
  answers/refund_reply.md         billing_service/refund_reply 1.10.0
  summary/thread_summary.md       member_service/thread_summary 2.0.0
  triage/intent_router.md         member_service/intent_router 1.0.0
  triage/escalation_note.md       member_service/escalation_note 1.9.0
tests/                    pytest：54 个用例，全部跑在 SQLite 上
docs/
  PROMPT-FRONTMATTER.md   文件头规范（与实现逐条对应）
  DELIVERY-EVIDENCE.md    验收项 → 实现 → 测试 → 真实输出 的对照表
  checks/                 真实命令输出落盘（pytest / cli-* / py-version / api-examples）
scripts/
  capture_checks.py       重新生成 docs/checks/*.txt
  capture_api_examples.py 重新生成 README 里引用的 HTTP 报文
  capture_checks_v2.py    重新生成 docs/checks/v2-*.txt 并断言每条命令的退出码
  capture_checks_v3.py    重新生成 docs/checks/v3-*.txt（AC3/AC4 两个可执行核对）
  verify_alias_only.py    核对 AC4：库里只有别名；--plant-address 做反向验证
  query_history.py        核对 AC3：打印两个查询接口的真实报文
```

---

## 4. 数据模型

四张表都遵循统一约定：`id`(UUID) / `org_id` / `site_id` / `created_at` / `created_by` / `updated_at` / `deleted_at`（软删除）。

### `ai_prompt_version`（不可变版本登记）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `tool` | `String(32)` | 业务工具，如 `member_service` |
| `prompt_key` | `String(64)` | 提示词标识，如 `faq_answer` |
| `version` | `String(32)` | 语义化版本原文 |
| `version_major/minor/patch` | `Integer` | 版本的数值分量，供排序与比较（**不靠字符串比较**） |
| `content_sha256` | `String(64)` | **只对正文**计算的哈希 |
| `file_path` | `String(512)` | 相对 `prompts/` 的路径 |
| `changelog` | `Text` | 变更说明，**不参与哈希** |

唯一约束 `uq_ai_prompt_version_scope (org_id, tool, prompt_key, version)`：同一租户下同版本只能有一行。

### `ai_model_profile`（只存别名）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `String(64)` | 配置名，如 `default` |
| `endpoint_alias` | `String(64)` | 别名，如 `model.main`；真实地址在 `MODEL_ALIAS_MODEL_MAIN` |
| `params` | `JSON`（PG 上是 `JSONB`） | `temperature` / `max_tokens` / `top_p` 等采样参数 |

### `eval_run` / `eval_result`

```
eval_run    (id, org_id, site_id, tool, status, started_at, finished_at,
             total_cases, passed_cases, created_by, …)
eval_result (id, org_id, run_id → eval_run, prompt_version_id → ai_prompt_version,
             model_profile_name, case_id, passed, …)
```

`eval_result.prompt_version_id` 是把“结果”钉在“版本”上的那一根钉子；
`runs/{id}/versions` 就是沿着它反查这次运行到底用了哪几版。
通过率一律从 `eval_result` 聚合，`eval_run.total_cases/passed_cases` 是同一次写入时算好的冗余，便于列表页。

---

## 5. `prompt-register` 命令

```bash
python -m app.cli_prompt_register \
    --prompt-dir prompts \
    [--dry-run] \
    [--allow-missing-db] \
    [--created-by <审计身份>]
```

**退出码就是与 CI 的契约**：

| 退出码 | 含义 | 排查方向 |
| --- | --- | --- |
| `0` | 成功（新插入，或全部已登记被跳过） | — |
| `1` | **内容问题**：文件头错误、版本未提升、版本回退、同版本两文件、正文为空 | 改提示词文件 |
| `2` | **环境问题**：DATABASE_URL 缺失、连不上、库未迁移、驱动缺失 | 改配置/跑迁移 |

规则要点（细则见 [docs/PROMPT-FRONTMATTER.md](docs/PROMPT-FRONTMATTER.md)）：

1. 哈希只算正文；改 `changelog` 不触发冲突；CRLF/LF 同一哈希；首尾空白忽略。
2. 版本号按 `(major, minor, patch)` **整数元组**比较，`1.10.0 > 1.9.0`。
3. 幂等：同版本同哈希 → `[skip]`，不插行，退出码 0。
4. 同版本不同哈希 → `code=version_content_conflict`，退出码 1，**整批不落库**（单事务）。
5. 版本回退 → `code=version_regression`，退出码 1。
6. 两文件同版本 → `code=duplicate_version`，退出码 1。
7. 文件头拼错（如 `versoin`）→ `code=unknown_field`，退出码 1，且给出 `did you mean 'version'` 提示。

真实输出见 `docs/checks/`：

| 文件 | 场景 | 退出码 |
| --- | --- | --- |
| `cli-ok.txt` | 首次登记 5 个文件 | 0 |
| `cli-idempotent.txt` | 再跑一次，全部 skipped，行数不变 | 0 |
| `cli-fail-version.txt` | 改了正文没升版本 | 1 |
| `cli-fail-rollback.txt` | 版本回退到 1.0.9 | 1 |
| `cli-fail-frontmatter.txt` | `versoin` 拼错 | 1 |
| `cli-dry-run-no-db.txt` | 无库 + `--dry-run --allow-missing-db` | 0 |
| `cli-exit2-unmigrated.txt` | 库连得上但没迁移 | 2 |
| `cli-exit2-unreachable.txt` | 库连不上 | 2 |

两次运行的行数对比（同一 SQLite 库）：

```
# run 1: docs/checks/cli-ok.txt
prompt-register: inserted 5, skipped 0, total 5
exit-code 0

# run 2: docs/checks/cli-idempotent.txt
[skip]    tool=billing_service prompt_key=refund_reply version=1.10.0 …
[skip]    tool=member_service prompt_key=escalation_note version=1.9.0 …
[skip]    tool=member_service prompt_key=faq_answer version=1.1.0 …
[skip]    tool=member_service prompt_key=intent_router version=1.0.0 …
[skip]    tool=member_service prompt_key=thread_summary version=2.0.0 …
prompt-register: inserted 0, skipped 5, total 5
exit-code 0
```

CI 中的位置（`.github/workflows/ci.yml`）：`migrate` → **`prompt-register`** → `eval-lint` → `pytest`。
登记必须先于 lint，因为 lint 是拿“已登记的版本哈希”去比对的；这一步失败意味着提示词文件本身有问题，
不该进入评分环节。

---

## 6. 两个查询接口

```
GET /api/eval/prompt-versions/{tool}/{prompt_key}/history
GET /api/eval/runs/{run_id}/versions
```

两个接口都按 `org_id` 隔离：`X-Org-Id` 请求头没带时回落到 `EVAL_ORG_ID`（再不设就是全零 UUID）。
**跨租户一律 404，而不是 403**——403 会泄露“这个 id 确实存在”，404 不会。

### 6.1 版本历史：每个版本最近一次评测的通过率

```bash
curl -s -H 'X-Org-Id: 11111111-1111-1111-1111-111111111111' \
  http://127.0.0.1:8000/api/eval/prompt-versions/member_service/faq_answer/history
```

真实响应（`python scripts/capture_api_examples.py` 输出，落盘于 `docs/checks/api-examples.txt`）：

```json
{
  "org_id": "11111111-1111-1111-1111-111111111111",
  "prompt_key": "faq_answer",
  "tool": "member_service",
  "versions": [
    {
      "created_at": "2026-11-22T12:59:16.778124",
      "latest_pass_rate": 0.5,
      "latest_run_finished_at": null,
      "latest_run_id": "ba1924ee-bc70-418b-80b1-e41aae55dbe8",
      "version": "1.0.0"
    },
    {
      "created_at": "2026-11-22T12:59:17.692900",
      "latest_pass_rate": 1.0,
      "latest_run_finished_at": null,
      "latest_run_id": "c564ef10-1258-4e94-bb19-20253132b7de",
      "version": "1.1.0"
    },
    {
      "created_at": "2026-11-22T12:59:18.550942",
      "latest_pass_rate": null,
      "latest_run_finished_at": null,
      "latest_run_id": null,
      "version": "1.2.0"
    }
  ]
}
```

读法：

- `1.0.0` 跑过一次，4 个用例过 2 个 → `0.5`；
- `1.1.0` 跑过一次，4 个全过 → `1.0`；
- `1.2.0` **从未评测** → `latest_pass_rate` 是 `null`，不是 `0`。
  `0` 会被读成“评测过且全错”，是完全相反的业务含义，所以这里坚持 null；分母为 0 时同样返回 `null`。
- 版本顺序按语义化数值升序，所以 `1.9.0` 会排在 `1.10.0` 前面（不是字符串序）。

### 6.2 某次运行用了哪些版本

```bash
curl -s -H 'X-Org-Id: 11111111-1111-1111-1111-111111111111' \
  http://127.0.0.1:8000/api/eval/runs/c564ef10-1258-4e94-bb19-20253132b7de/versions
```

```json
{
  "run_id": "c564ef10-1258-4e94-bb19-20253132b7de",
  "status": "finished",
  "tool": "member_service",
  "versions": [
    {
      "content_sha256": "747164aa2f08e3bc34efda34c84a1fb554b37496be2af814a44e750482c38a9c",
      "prompt_key": "faq_answer",
      "tool": "member_service",
      "version": "1.1.0"
    }
  ]
}
```

这里返回的是**该次运行 `eval_result` 实际引用的版本**（去重后按 `tool`、`prompt_key`、版本升序），
不是库里全部已登记版本——这才是定位“退化由哪一版引入”的正确口径。

跨租户访问同一运行：

```bash
curl -s -H 'X-Org-Id: 22222222-2222-2222-2222-222222222222' \
  http://127.0.0.1:8000/api/eval/runs/c564ef10-1258-4e94-bb19-20253132b7de/versions
# HTTP 404
# {"detail": "no evaluation run c564ef10-1258-4e94-bb19-20253132b7de in this organisation"}
```

运行不存在同样返回 404；`tool`/`prompt_key` 路径参数含非法字符返回 422（路径校验正则）。

---

## 7. 对接点（被测服务与执行器）

### 7.1 被测服务：评测模式下回显 prompt_version 与 model_profile

```python
from app.eval_binding import build_eval_context

binding = build_eval_context(
    session,
    org_id=org_id,
    tool="member_service",
    prompt_key="faq_answer",
    version="1.1.0",
    model_profile="default",
)
response["eval"] = binding.as_payload()
```

评测模式响应里就多出这一段（真实输出，见 `docs/checks/api-examples.txt`）：

```json
{
  "model_profile": {
    "endpoint_alias": "model.main",
    "name": "default",
    "params": {"max_tokens": 512, "temperature": 0.2}
  },
  "prompt_version": {
    "content_sha256": "747164aa2f08e3bc34efda34c84a1fb554b37496be2af814a44e750482c38a9c",
    "file_path": "answers/faq_answer.md",
    "id": "9ff9db5e-af62-4de5-af06-fc6b548b8edc",
    "prompt_key": "faq_answer",
    "tool": "member_service",
    "version": "1.1.0"
  }
}
```

注意 `model_profile` **只有别名和采样参数**，没有地址：真正调用模型时才 `binding.resolve_endpoint()`，
由 `config.resolve_model_endpoint` 从 `MODEL_ALIAS_MODEL_MAIN` 之类环境变量取地址。
这样一条评测记录在库里永远不含环境相关信息，换个部署环境也不用改数据。

### 7.2 执行器：写 eval_run 与 eval_result

```python
from app.eval_binding import CaseOutcome, record_eval_run

run = record_eval_run(
    session,
    org_id=org_id,
    site_id=site_id,
    created_by="executor@example.invalid",
    tool="member_service",
    status="finished",
    started_at=started,
    finished_at=finished,
    model_profile_name="default",
    outcomes=[
        CaseOutcome("case-001", prompt_version_id, True),
        CaseOutcome("case-002", prompt_version_id, False),
    ],
)
session.commit()   # 事务由调用方拥有：run 与 result 一起提交
```

要点：

- `outcomes` 里的 `prompt_version_id` 来自被测服务回显的 `prompt_version.id`（不是执行器自己猜的版本）。
- `total_cases` / `passed_cases` 由 `outcomes` 推导，不会与 `eval_result` 行数矛盾。
- 执行器不 `commit`，由调用方决定事务边界。

---

## 8. 测试

```bash
python -m pytest -q        # 54 passed
```

全部用例跑在 SQLite 上，**不依赖 PostgreSQL 服务，也不依赖 psycopg**：models 只用通用类型
（`Uuid` / `JSON` / `String` / `DateTime`），`params` 列通过 `JSON().with_variant(JSONB(), "postgresql")`
在 PG 上自动变成 JSONB；`app/db.py` 只在真正建连接时才 import 驱动。

覆盖矩阵（函数名即断言点）：

| 验收点 | 用例 |
| --- | --- |
| 首次登记插入 N 行 | `test_prompt_register.py::test_first_registration_inserts_one_row_per_file` |
| 幂等（两次，第二次 skipped） | `test_second_run_is_idempotent_and_reports_skipped` |
| 改 changelog 不算内容变更 | `test_changing_changelog_only_keeps_the_hash_stable` |
| 改正文未升版本 → 1 且不写库 | `test_content_change_without_version_bump_exits_1_and_writes_nothing` |
| 版本回退 → 1 | `test_version_rollback_exits_1`、`test_rollback_of_one_file_prevents_writing_the_whole_batch` |
| 同版本两文件 → 1 | `test_same_version_in_two_files_exits_1` |
| 缺字段 / 未知字段 / 版本非三段 → 1 | `test_missing_frontmatter_field_exits_1`、`test_unknown_frontmatter_field_exits_1`、`test_version_with_two_segments_exits_1` |
| CRLF 与 LF 同哈希 | `test_crlf_and_lf_versions_of_the_same_body_hash_identically`、`test_frontmatter.py::test_crlf_and_lf_bodies_hash_identically` |
| 1.10.0 > 1.9.0 | `test_frontmatter.py::test_semver_compares_numerically_not_lexicographically` |
| 无库/未迁移/连不上 → 2 | `test_missing_database_configuration_exits_2`、`test_unmigrated_database_exits_2`、`test_unreachable_database_exits_2` |
| `--dry-run` / `--allow-missing-db` | `test_dry_run_reports_plan_without_writing`、`test_allow_missing_db_validates_files_without_a_database` |
| history：通过率 / null / 跨 org 404 | `test_api_eval.py::test_history_returns_one_row_per_version_with_pass_rates`、`test_history_returns_null_pass_rate_when_never_evaluated`、`test_history_returns_null_pass_rate_when_denominator_is_zero`、`test_history_is_404_across_orgs` |
| history：取“最近一次”而非“第一次” | `test_history_uses_the_most_recent_run_not_the_first` |
| runs/{id}/versions：只返该次运行的版本 / 404 | `test_run_versions_returns_only_the_versions_the_run_used`、`test_run_versions_is_404_for_unknown_run`、`test_run_versions_is_404_across_orgs` |
| eval_binding 只回别名，地址走环境变量 | `test_eval_binding.py::test_build_eval_context_returns_version_and_alias_only`、`test_alias_resolves_only_through_the_environment` |
| 依赖方向：无 http(s) 字面量、无外发 SDK | `test_dependency_rules.py::test_no_hard_coded_network_addresses`、`test_no_third_party_egress_sdk_is_imported`、`test_no_credentials_or_dsns_literals` |
| 端到端：1.0.0 → 1.1.0 全链路 | `test_scenario_demo.py::test_register_v1_then_promote_to_v1_1_and_query_history` |

`test_dependency_rules.py` 用 `ast` 扫描 `app/` 源码，断言：源码里没有 `http://`/`https://` 字面量、
没有 `requests` / `httpx` / `openai` / `anthropic` / `boto3` 之类外发 SDK 的 import、
没有把 DSN 或口令写死的字面量。这条测试是“不向境外发送数据”“不硬编码地址”两条约束的机器校验。

---

## 9. 已知边界

1. **本机没有 PostgreSQL 服务，也没有安装 psycopg**。所有自动化验证（54 个用例 + 全部 CLI 证据）都在 SQLite 上完成。
   PostgreSQL 侧的保证来自：Alembic 迁移使用 `postgresql` 方言（`JSONB`、`sa.Uuid`），
   以及 models 里 `JSON().with_variant(JSONB(), "postgresql")`。CI 配置里带 `pgvector/pgvector:pg16` 服务容器，
   会在真实 PG 上跑 `alembic upgrade head` + 全量测试。**本机未实跑 PG 的三条路径**：Alembic 迁移、JSONB 落盘、
   psycopg 驱动装载——这三项需要在有 PG 的环境用 CI 复核。
2. **未接 Redis**。本单范围（版本登记 + 两个只读查询）没有缓存或队列需求，
   引入 Redis 只会增加部署面而无收益；表结构、接口均不依赖它，后续需要时可直接加缓存层。
3. **迁移只有一条 revision**（`0001_prompt_registry`）。四张表一次建齐，没有历史包袱；
   后续变更按常规新增 revision，`migrations/env.py` 从 `DATABASE_URL` 读连接串。
4. **`eval_run` / `eval_result` 只做写入与查询，不含评分逻辑**。评分由评测执行器负责，
   本单只保证“结果能挂到正确的版本上”。
5. **`history` 接口一次返回该提示词的全部版本**，未分页。单个提示词的版本量级在几十以内（本单 1500 工单场景），
   加 `limit/offset` 反而让“定位退化”多一步；若后续单 key 版本数上百，应加分页参数。
6. **`org_id` 目前从请求头/环境变量取**，没有接入统一鉴权中间件。已验证的语义是“不同 org 互相不可见且一律 404”，
   生产接入网关鉴权后只需替换 `caller_org_id` 的取值来源，隔离逻辑本身不变。
7. **`runs/{id}/versions` 只按 `tool`、`prompt_key`、版本号排序**，不返回每个版本在该次运行内的用例数/通过数。
   需要版本级对比时用 `history` 接口配合运行 id 反查。

---

## 10. 交付物清单

| # | 交付物 | 位置 | 证据 |
| --- | --- | --- | --- |
| 1 | 表迁移（Alembic，PostgreSQL 方言） | `migrations/versions/0001_prompt_registry.py`、`alembic.ini`、`migrations/env.py` | `make migrate` / `make migrate-sql`；SQLite 等价物见 `docs/checks/db-init.txt` |
| 2 | `prompt-register` 命令 | `app/cli_prompt_register.py`、`app/registry.py`、`app/frontmatter.py` | `docs/checks/cli-ok.txt`、`cli-idempotent.txt`、`cli-fail-*.txt`、`cli-exit2-*.txt` |
| 3 | 两个查询接口 | `app/api_eval.py`、`app/schemas.py`、`app/main.py` | `docs/checks/api-examples.txt`、`tests/test_api_eval.py`（11 例） |
| 4 | 文件头规范说明 | `docs/PROMPT-FRONTMATTER.md` | 与 `app/frontmatter.py` 逐条对应表；`tests/test_frontmatter.py`（10 例） |
| 5 | pytest 测试 | `tests/`（54 例） | `docs/checks/pytest.txt` |
| 6 | README（本文件） | `README.md` | 一键命令、环境变量、目录结构、真实 HTTP 报文、对接点、边界 |

补充产物：`app/cli_model_profile.py`（登记模型配置，只存别名）、`app/eval_binding.py`（被测服务与执行器的对接面）、
`app/dbcheck.py`（退出码 2 的来源）、`scripts/capture_checks.py` 与 `scripts/capture_api_examples.py`（可复现的证据生成脚本）、
`docs/DELIVERY-EVIDENCE.md`（验收项对照表）。

---

## 11. 复现全部证据

```bash
python scripts/capture_checks.py        # 重写 docs/checks/*.txt（pytest、cli-*、py-version）
python scripts/capture_api_examples.py  # 重写 docs/checks/api-examples.txt（本 README 引用的报文）
```

两个脚本都用临时目录里的独立 SQLite 库，不会污染开发库，也不需要 PostgreSQL。
脚本会检查每个命令的退出码是否符合预期，不符合则自身退出码非 0。

补充证据（第二轮，针对 delivery_check 的三项 unclear）用一条命令重跑：

```bash
python scripts/capture_checks_v2.py     # 重写 docs/checks/v2-*.txt（10 个文件）并自检退出码
```

它逐条捕获「依赖与运行环境 / 四个 AC 的复现步骤 / 实测数字」，末行打印
`all captured commands matched their expectations` 才算通过；对应的说明与逐条对照表见
[docs/DELIVERY-EVIDENCE.md](docs/DELIVERY-EVIDENCE.md) 的「补充证据 v2」一节。
数据落在 `build/v2/db/`（已在 `.gitignore` 内），跑完不会在仓库里留下数据库文件。

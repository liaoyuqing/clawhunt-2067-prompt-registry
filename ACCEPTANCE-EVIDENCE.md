# 验收证据核对表（ACCEPTANCE-EVIDENCE）

本文件回答自动预检提出的三条 unclear（2026-11-22T15:44:40Z，commit `c607428`）：

| 镜头 | 状态 | 平台 advice 原文 |
| --- | --- | --- |
| feasibility | unclear | Specify dependencies, access requirements and a feasible implementation path. |
| acceptance | unclear | Address every customer acceptance criterion, including constraints and edge cases. |
| evidence | unclear | Provide reproducible verification steps and the evidence required by the task. |

组织方式：第 1 节是一条 AC 一行的核对表（AC 原文 / 实现位置 / pytest 节点 id 与完整命令 / 断言的具体值 / 证据文件）；
第 2、3、4 节分别对应上面三条 advice。所有数字都是在本仓库的干净 clone 上跑出来的，
每条命令都能原样复制执行。

---

## 1. 四条验收标准的核对表

### AC1 —— 修改提示词内容但不升版本号时 prompt-register 退出码非 0

| 项 | 内容 |
| --- | --- |
| AC 原文 | 1. 修改提示词内容但不升版本号时 prompt-register 退出码非 0（测试通过） |
| 实现位置 | `app/registry.py:174` `plan_actions()` 里 `existing.content_sha256 == prompt.content_sha256` 判等（`:188-203` 判为 `skip`，`:204-217` 生成 `RegistryError("version_content_conflict", …)`）；`app/cli_prompt_register.py:75` `main()` 在 `:136-143` 走 `session.rollback()` 并 `return _fail(..., exit_code=EXIT_CONTENT)`；整批唯一的 `session.commit()` 在 `app/cli_prompt_register.py:152-156`，因而不可能写进半批数据 |
| 退出码常量 | `app/cli_prompt_register.py:32-34`：`EXIT_OK = 0`、`EXIT_CONTENT = 1`、`EXIT_OPERATIONAL = 2` |
| pytest 节点 id | `tests/test_prompt_register.py::test_content_change_without_version_bump_exits_1_and_writes_nothing` |
| 完整命令 | `python -m pytest -q tests/test_prompt_register.py::test_content_change_without_version_bump_exits_1_and_writes_nothing` |
| 应看到 | `1 passed`（本轮实测 `1 passed in 0.10s`），退出码 0 |
| 断言的具体值 | `assert code == 1`（即 `EXIT_CONTENT`）；`assert "code=version_content_conflict" in err`；`assert database.count_prompt_versions() == count_before`（行数不变）；`assert stored.content_sha256 == content_sha256(PROMPT_BODY_V1)`（库里仍是旧正文的哈希） |
| 边界与对照组 | 同名改动把版本提到 `1.2.0` 必须被接受：`tests/test_prompt_register.py::test_version_rollback_exits_1` 与 `::test_same_version_in_two_files_exits_1`、`::test_rollback_of_one_file_prevents_writing_the_whole_batch` 覆盖回退、两文件同版本、一个文件出错整批不写；`::test_changing_changelog_only_keeps_the_hash_stable` 覆盖「只改 changelog 不算内容变更」 |
| 证据文件 | `docs/checks/cli-fail-version.txt`（真实报文里比较的两个前缀是 `58a3ed4f3f57...` 与 `193ef74e0b25...`）、`docs/checks/v2-ac1-conflict.txt`（`5 → 5` 行、`exit-code 1`）、对照组 `docs/checks/v2-ac1-bumped-ok.txt`（`5 → 6` 行、`exit-code 0`）、`docs/checks/cli-fail-rollback.txt` |

### AC2 —— 同一内容重复登记不产生重复记录

| 项 | 内容 |
| --- | --- |
| AC 原文 | 2. 同一内容重复登记不产生重复记录（pytest 通过） |
| 实现位置 | `app/registry.py:174` `plan_actions()` 中 `:188-203`：同 `(tool, prompt_key, version)` 且 `content_sha256` 相同 → `action="skip"`，`reason="already registered with identical content"`，不进入插入列表；数据库侧兜底为 `app/models.py:65-70` 的 `UniqueConstraint("org_id", "tool", "prompt_key", "version", name="uq_ai_prompt_version_scope")`；哈希口径在 `app/frontmatter.py:102-110`（`normalize_text` 把 CRLF/CR 折成 LF 再 strip，`content_sha256` 只对正文计算，front-matter 不参与） |
| pytest 节点 id | `tests/test_prompt_register.py::test_second_run_is_idempotent_and_reports_skipped` |
| 完整命令 | `python -m pytest -q tests/test_prompt_register.py::test_second_run_is_idempotent_and_reports_skipped` |
| 应看到 | `1 passed`（本轮实测 `1 passed in 0.11s`），退出码 0 |
| 断言的具体值 | `assert first_code == 0 and second_code == 0`；`assert "inserted 2, skipped 0" in first_out`；`assert "inserted 0, skipped 2" in second_out`；`assert "[skip]" in second_out`；`assert database.count_prompt_versions() == count_after_first == 2`（第二次跑完仍是 2 行） |
| 边界与对照组 | `tests/test_frontmatter.py::test_crlf_and_lf_bodies_hash_identically`、`::test_hash_ignores_frontmatter_and_trailing_whitespace`、`tests/test_prompt_register.py::test_crlf_and_lf_versions_of_the_same_body_hash_identically` 覆盖「换行符不同但正文相同仍是同一版本」；`tests/test_frontmatter.py::test_semver_compares_numerically_not_lexicographically` 覆盖 `1.10.0 > 1.9.0`（不靠字符串比较） |
| 证据文件 | `docs/checks/cli-idempotent.txt`（5 条 `[skip]` 与 `inserted 0, skipped 5, total 5`）、`docs/checks/v2-ac2-idempotent.txt`（`0 → 5` 后 `5 → 5`，两次退出码均为 0）、首次登记 `docs/checks/cli-ok.txt` |

### AC3 —— history 接口返回各版本对应的最近通过率

| 项 | 内容 |
| --- | --- |
| AC 原文 | 3. history 接口返回各版本对应的最近通过率（接口测试断言） |
| 实现位置 | 路由 `app/api_eval.py:161` `prompt_version_history()`（装饰器 `:152-160` 声明 `/prompt-versions/{tool}/{prompt_key}/history`）；「最近一次」的选取在 `app/api_eval.py:87` `_latest_evaluation_per_version()`（排序键 `finished_at` → `started_at` → `created_at` → run id，绝不按插入顺序）；通过率计算在 `app/api_eval.py:210`：`rate = (passed / total) if total > 0 else None`；响应契约 `app/schemas.py:11-30`（`extra="forbid"`，字段说明写明「null when the version was never evaluated … never 0.0 as a stand-in」）；配套的 `app/api_eval.py:244` `run_versions()` 反查某次运行用了哪些版本 |
| pytest 节点 id | `tests/test_api_eval.py::test_history_uses_the_most_recent_run_not_the_first`（主断言）；配套 `::test_history_returns_one_row_per_version_with_pass_rates`、`::test_history_returns_null_pass_rate_when_never_evaluated`、`::test_history_returns_null_pass_rate_when_denominator_is_zero`、`::test_history_is_404_across_orgs`、`::test_run_versions_returns_only_the_versions_the_run_used` |
| 完整命令 | `python -m pytest -q tests/test_api_eval.py::test_history_uses_the_most_recent_run_not_the_first` |
| 应看到 | `1 passed`（本轮实测 `1 passed in 0.06s`），退出码 0 |
| 断言的具体值 | 最近一次测试里旧运行全错、新运行全对：`assert item["latest_run_id"] == str(new.id)`、`assert item["latest_run_id"] != str(old.id)`、`assert item["latest_pass_rate"] == 1.0`；「每版一行」用例里两个版本分别是 `0.5` 与 `0.75`（4 个用例过 2 个 / 过 3 个）；从未评测的版本 `assert versions["1.1.0"]["latest_pass_rate"] is None`（不是 `0.0`）；分母为 0 时同样 `is None` |
| 边界与对照组 | 跨租户访问同一提示词与同一运行都断言 `status_code == 404`（不是 403，避免泄露 id 是否存在）；路径参数非法断言 422；`runs/{id}/versions` 只返回该次运行实际引用过的版本，而库里已登记更多版本 |
| 证据文件 | `docs/checks/api-examples.txt`（两个接口的真实 JSON 报文，含 `"latest_pass_rate": null` 与跨租户 404）、`docs/checks/v2-ac3-history-runs.txt`（三趟 0.6000 / 0.9000 / 0.8000）、`docs/checks/v2-ac3-api-responses.txt`（history 返回 `0.8`、未评测版本 `null`、`runs/{id}/versions` 只 1 条而库存 6 版）；一键复跑：`python scripts/query_history.py build/demo/run.sqlite3` |

### AC4 —— 数据库中不存在模型的真实地址，只有别名

| 项 | 内容 |
| --- | --- |
| AC 原文 | 4. 数据库中不存在模型的真实地址，只有别名（表结构与测试检查） |
| 实现位置 | 表结构 `app/models.py:73` `class AiModelProfile`，字段只有 `name` / `endpoint_alias` / `params`（`:78-81`），类注释即契约：「A named sampling profile. Only an alias is stored, never an address.」；迁移同列 `migrations/versions/0001_prompt_registry.py:65-83`（无 url / base_url 列）；写入口 `app/cli_model_profile.py:56` `main()` 在 `:73-79` 拒绝含 `/` 或 `:` 的「别名」（`endpoint_alias must be a plain alias such as model.main, not an address`，`exit_code=EXIT_CONTENT`）；地址只从环境变量取：`app/config.py:104` `alias_env_var()`（`model.main` → `MODEL_ALIAS_MODEL_MAIN`）与 `app/config.py:114` `resolve_model_endpoint()`（未配置时抛 `ConfigError`，无默认值）；评测响应只回别名 `app/eval_binding.py:67` `ModelProfileBinding` 的 `as_payload()`（`:74-79`，字段仅 `name` / `endpoint_alias` / `params`） |
| pytest 节点 id | `tests/test_model_profile.py::test_model_profile_rejects_an_address_as_alias`（写入口拒绝地址）+ `tests/test_dependency_rules.py::test_no_hard_coded_network_addresses`（源码里没有 `http://` / `https://`） |
| 完整命令 | `python -m pytest -q tests/test_model_profile.py::test_model_profile_rejects_an_address_as_alias tests/test_dependency_rules.py::test_no_hard_coded_network_addresses` |
| 应看到 | `2 passed`（本轮实测 `2 passed in 0.10s`），退出码 0 |
| 断言的具体值 | 拒绝地址：`assert code == 1`、`assert "plain alias" in err`、`assert session.execute(select(AiModelProfile)).scalars().all() == []`（一行都没写）；静态扫描：遍历 `app/**/*.py`，`offenders == []` 才算过，`offenders` 的构造是「文件里出现 `http://` 或 `https://` 就记一条 `name:line contains scheme`」。另有 `tests/test_model_profile.py::test_model_profile_registers_alias_and_params` 断言落库行 `row.endpoint_alias == "model.main"`、`row.params == {"temperature": 0.2, "max_tokens": 512}`；`tests/test_eval_binding.py::test_alias_resolves_only_through_the_environment` 断言解析只走环境变量 |
| 边界与对照组 | `tests/test_dependency_rules.py::test_no_credentials_or_dsns_literals` 同时扫描 `postgresql://`、`postgresql+psycopg://`、`redis://`、`password=`、`api_key=` 字面量；`tests/test_model_profile.py::test_model_profile_second_run_updates_the_same_row` 保证同名配置是更新而不是新增行（`len(rows) == 1`） |
| 证据文件 | `docs/checks/v2-ac4-alias-only.txt`（`ai_model_profile` 列为 `id,name,endpoint_alias,params,org_id,site_id,created_at,created_by,updated_at,deleted_at`，全库 `http literal hits: 0`，未设环境变量时 `ConfigError` 退出码 1）、`docs/checks/api-examples.txt`（报文里的 `model_profile` 只有 `name` / `endpoint_alias` / `params`）、`docs/checks/py-version.txt`；一键复跑：`python scripts/verify_alias_only.py build/demo/run.sqlite3`（退出码 0） |

---

## 2. 依赖与运行环境（对应 feasibility）

> advice 原文：Specify dependencies, access requirements and a feasible implementation path.

### 2.1 解释器与包

| 项 | 值 |
| --- | --- |
| Python | `3.11.9`（`pyproject.toml` 声明 `requires-python = ">=3.11"`） |
| 运行时依赖 `requirements.txt` | `fastapi>=0.115,<1.0`、`uvicorn>=0.30,<1.0`、`SQLAlchemy>=2.0.30,<2.1`、`alembic>=1.13,<2.0`、`pydantic>=2.7,<3.0`、`PyYAML>=6.0,<7.0` |
| 测试依赖 `requirements-dev.txt` | 追加 `pytest>=8.0,<10.0`、`httpx>=0.27,<1.0` |
| 生产额外依赖 `requirements-postgres.txt` | 追加 `psycopg[binary]>=3.1,<4.0` |
| 本机实测版本 | alembic 1.18.4 / fastapi 0.136.1 / httpx 0.28.1 / pydantic 2.14.4 / pytest 9.2.0 / PyYAML 6.0.2 / SQLAlchemy 2.0.49 / uvicorn 0.47.0 |

数据库驱动请注意：本仓库用的是 **psycopg 3**（包名 `psycopg`，URL 前缀 `postgresql+psycopg://`），
不是 psycopg2（包名 `psycopg2-binary`，URL 前缀 `postgresql+psycopg2://`）。`requirements-postgres.txt` 与 `pyproject.toml`
的 `postgres` extra 都写作 `psycopg[binary]>=3.1,<4.0`，两者一致。

### 2.2 数据库：SQLite 用于自测，PostgreSQL 用于生产

| 场景 | 命令 | 是否需要 psycopg |
| --- | --- | --- |
| 跑测试（含全部接口测试） | `python -m pytest -q` | 不需要：`tests/conftest.py:84-94` 的 `database` fixture 用 `tmp_path` 建文件型 SQLite 并 `Base.metadata.create_all` 建表；接口测试用 `:97-109` 的 `memory_database`（内存库 + `StaticPool`） |
| 本机手工演示 | `DATABASE_URL=sqlite:///./dev.sqlite3 python -m app.dbcheck init` | 不需要 |
| 生产迁移 | `pip install -r requirements-postgres.txt` 后 `DATABASE_URL=postgresql+psycopg://… python -m alembic upgrade head` | 需要 |

模型层刻意只用通用类型（`sa.Uuid` / `sa.JSON` / `sa.String` / `sa.DateTime`），`params` 列通过
`JSON().with_variant(postgresql.JSONB(...), "postgresql")` 在 PG 上自动变 JSONB，所以同一套代码在两种库上都能建表。
`app/db.py` 只在真正要建连接时才 import 驱动。

### 2.3 缺少依赖时会发生什么（实测，不是推断）

本仓库的测试里**没有任何** `pytest.mark.skip` / `pytest.mark.skipif` / `pytest.importorskip` / `xfail`
（对 `tests/` 全目录 grep 该四类标记，命中 0 处）。因此「缺依赖就跳过一部分用例」这件事在本仓库不会发生，
真实行为是用例收集阶段直接失败。逐个屏蔽（用 `sys.meta_path` 拦截该模块的 import）后实测：

| 被屏蔽的模块 | 结果 | 原因 |
| --- | --- | --- |
| `httpx` | 收集失败，退出码 4 | `tests/conftest.py:19` `from fastapi.testclient import TestClient` → starlette 的 testclient 需要 httpx，报 `ImportError` |
| `pydantic` | 收集失败，退出码 4 | `app/schemas.py` 的响应模型全建在 pydantic v2 上 |
| `fastapi` | 收集失败，退出码 4 | 应用本身 |
| `yaml` | 收集失败，退出码 3 | `app/frontmatter.py` 解析 front-matter 需要 PyYAML |
| `alembic` | **不受影响，仍然 54 passed** | 测试用 SQLAlchemy metadata 直接建表，只有生产迁移才走 alembic |

换句话说：`requirements-dev.txt` 里除 alembic 外的每一项都是跑测试的硬依赖；alembic 只在 `make migrate` 时才被用到。

### 2.4 访问要求（部署方需要什么）

| 项 | 要求 |
| --- | --- |
| 数据库账号 | 一个能在目标库建表、并对四张表读写删的账号；不需要超级用户，不需要建库权限以外的额外授权 |
| 环境变量 | `DATABASE_URL`、`PROMPT_CREATED_BY` 必填；`EVAL_ORG_ID` / `EVAL_SITE_ID` / `EVAL_MODEL_PROFILE` 可选；`MODEL_ALIAS_<ALIAS>` 由部署方提供真实地址 |
| 网络 | 服务只监听本地端口（默认 `127.0.0.1:8000`），不主动外发任何请求；`tests/test_dependency_rules.py` 用 AST 扫描锁死「不得 import 外发 SDK」与「源码不得出现地址字面量」 |
| 可选组件 | 无。本单范围不需要 Redis、对象存储、消息队列 |

### 2.5 可实施的落地路径

1. **建表**：`pip install -r requirements-postgres.txt` → `DATABASE_URL=postgresql+psycopg://… python -m alembic upgrade head`（幂等，重复执行无副作用，只有一条 revision `0001_prompt_registry`）。
2. **登记提示词**：`python -m app.cli_prompt_register --prompt-dir prompts`。必须先于 CI 的 `eval-lint`，因为 lint 拿的是已登记版本的哈希；这一步失败说明提示词文件本身有问题，不该进入评分环节（`.github/workflows/ci.yml:36-43`）。
3. **登记模型配置**：`python -m app.cli_model_profile --name default --endpoint-alias model.main --temperature 0.2 --max-tokens 512`；真实地址由部署方写入 `MODEL_ALIAS_MODEL_MAIN`，不落库。
4. **接被测服务**：评测模式下调 `app.eval_binding.build_eval_context(...)`（`app/eval_binding.py:138`），把 `prompt_version` 与 `model_profile` 放进响应。
5. **接执行器**：调 `app.eval_binding.record_eval_run(...)`（`app/eval_binding.py:179`）写 `eval_run` 与 `eval_result`；函数内部不 commit，事务边界由调用方决定。
6. **查询**：`GET /api/eval/prompt-versions/{tool}/{prompt_key}/history` 与 `GET /api/eval/runs/{id}/versions`。
7. **回滚**：把 `MODEL_ALIAS_*` 指回旧地址即可，数据库不用动（库里从来没有地址）。

---

## 3. 逐条 AC 的可复现步骤（对应 evidence）

> advice 原文：Provide reproducible verification steps and the evidence required by the task.

### 3.0 从干净 clone 开始

```bash
git clone https://github.com/liaoyuqing/clawhunt-2067-prompt-registry.git
cd clawhunt-2067-prompt-registry
python -m pip install -r requirements-dev.txt
python -m pytest -q                       # → 54 passed，退出码 0
```

下面四条彼此独立，每条都能单独复制执行；需要数据库的那几条先建一个 SQLite 库：

```bash
mkdir -p build/demo
export DATABASE_URL=sqlite:///./build/demo/run.sqlite3
export PROMPT_CREATED_BY=platform-ops@example.invalid
python -m app.dbcheck init                # → schema ready in sqlite:///./build/demo/run.sqlite3
python -m app.cli_prompt_register --prompt-dir prompts   # → inserted 5, skipped 0, total 5
```

（`mkdir -p` 不是多余的：SQLite 不会自建父目录，目录不存在时会报 `unable to open database file`。）

本节命令块按 bash 书写（Linux / macOS / CI 一致）。在 Windows PowerShell 里等价替换为：
`export X=Y` → `$env:X='Y'`，`mkdir -p d` → `New-Item -ItemType Directory -Force -Path d`，
`echo "exit=$?"` → `$LASTEXITCODE`（必须在同一行紧跟该命令读取）。

### 3.1 复现 AC1

```bash
# 复制一份提示词，只改正文、不动 version（这一行在 bash 与 PowerShell 里都能直接跑；
# 用 chr(13)/chr(10) 拼换行是为了避开两种 shell 对引号与转义的不同处理）
python -c "import io, shutil; shutil.copytree('prompts', 'build/demo/ac1_prompts', dirs_exist_ok=True); p='build/demo/ac1_prompts/answers/faq_answer.md'; t=io.open(p, encoding='utf-8', newline='').read().replace(chr(13)+chr(10), chr(10)); lines=t.split(chr(10)); end=[i for i in range(1, len(lines)) if lines[i].strip()=='---'][0]; io.open(p, 'w', encoding='utf-8', newline=chr(10)).write(chr(10).join(lines[:end+1] + ['', 'One extra body line; the version stays 1.1.0.'] + lines[end+1:])); print('body edited, front-matter untouched')"
# → body edited, front-matter untouched

python -m app.cli_prompt_register --prompt-dir build/demo/ac1_prompts; echo "exit=$?"
# → [fail] code=version_content_conflict … (58a3ed4f3f57... vs …)
# → [error] 1 content error(s); nothing was written
# → exit=1

python -m pytest -q tests/test_prompt_register.py::test_content_change_without_version_bump_exits_1_and_writes_nothing
# → 1 passed
```

想看行数不变：同一份库上先记下 `select count(*) from ai_prompt_version` 再跑一次上面的命令即可；
`docs/checks/v2-ac1-conflict.txt` 里记录的实测是 `5 → 5`。

### 3.2 复现 AC2

```bash
python -m app.cli_prompt_register --prompt-dir prompts   # 第一次：inserted 5, skipped 0, total 5
python -m app.cli_prompt_register --prompt-dir prompts   # 第二次：inserted 0, skipped 5, total 5，五行 [skip]
echo "exit=$?"                                          # → exit=0

python -m pytest -q tests/test_prompt_register.py::test_second_run_is_idempotent_and_reports_skipped
# → 1 passed
```

### 3.3 复现 AC3

```bash
# 造三趟同一版本 1.1.0 的评测运行：3/5=0.6、9/10=0.9、4/5=0.8，finished_at 依次递增
python scripts/replay_history.py build/demo/run.sqlite3
# → seeded run … passed=3 total=5 rate=0.6000
# → seeded run … passed=9 total=10 rate=0.9000
# → seeded run … passed=4 total=5 rate=0.8000

# 直接问接口（不需要起服务、不需要 curl、不需要挑端口：走 FastAPI 的 TestClient）。
# 脚本自己把 history 返回的 latest_run_id 接着用于后三次调用，所以不用手抄 UUID。
python scripts/query_history.py build/demo/run.sqlite3
# → $ curl -s /api/eval/prompt-versions/member_service/faq_answer/history
#   HTTP 200
#   { "tool": "member_service", "prompt_key": "faq_answer",
#     "versions": [ { "version": "1.1.0", "latest_pass_rate": 0.8,
#                     "latest_run_finished_at": "2026-11-22T09:23:00",
#                     "latest_run_id": "（脚本打印的真实 UUID，每次 replay 重新生成）" } ] }
# → latest_pass_rate=0.8 for version 1.1.0
# → 接着 $ curl -s /api/eval/runs/…/versions                → HTTP 200，只有 1 个版本
#   （库里已登记 5 个版本，接口只回这次运行实际用过的那一个）
# → 接着同样 URL 加 -H 'X-Org-Id: 22222222-2222-2222-2222-222222222222' → HTTP 404
# → 接着 $ curl -s /api/eval/runs/00000000-0000-0000-0000-000000000000/versions → HTTP 404
# → all query endpoints behaved as documented，退出码 0
```

想把服务真跑起来看同样的报文也可以（端口自选）：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
RUN=$(curl -s 'http://127.0.0.1:8000/api/eval/prompt-versions/member_service/faq_answer/history' \
      | python -c 'import json,sys; print(json.load(sys.stdin)["versions"][0]["latest_run_id"])')
curl -s "http://127.0.0.1:8000/api/eval/runs/$RUN/versions"
curl -s -H 'X-Org-Id: 22222222-2222-2222-2222-222222222222' \
     "http://127.0.0.1:8000/api/eval/runs/$RUN/versions"    # → 404
```

跨租户与不存在的运行一律 404（不是 403），这样接口不会泄露某个 id 是否存在。

只看接口层断言、不起服务也可以：

```bash
python -m pytest -q tests/test_api_eval.py
# → 11 passed
```

### 3.4 复现 AC4

```bash
python -m app.cli_model_profile --name model.main --endpoint-alias model.main \
  --temperature 0.2 --max-tokens 1024
# → model-profile: insert name=model.main alias=model.main params={"max_tokens": 1024, "temperature": 0.2}
#    address_from_env=MODEL_ALIAS_MODEL_MAIN

# 一、表结构里没有地址列（本机没有 sqlite3 CLI，用 python 读同样的信息）
python -c "import sqlite3;c=sqlite3.connect('build/demo/run.sqlite3');print([r[1] for r in c.execute('PRAGMA table_info(ai_model_profile)')])"
# → ['id','name','endpoint_alias','params','org_id','site_id','created_at','created_by','updated_at','deleted_at']
#   逐列比对：没有 url / base_url / endpoint_url / address 任何一个

# 二、代码 + 库一起查：scripts/verify_alias_only.py 把四件事一次做完并给出退出码
#     （列名里没有地址列 / 四张表所有字符串列没有 http(s) 字面量 /
#      别名只能从 MODEL_ALIAS_* 解析且没有内置默认值 / ORM 模型也没有地址字段）
python scripts/verify_alias_only.py build/demo/run.sqlite3
# → [1] ai_model_profile columns: id, name, endpoint_alias, params, org_id, site_id,
#       created_at, created_by, updated_at, deleted_at
#       no url/base_url/endpoint_url/address column; only endpoint_alias
# → [2] scanned 369 value(s) across 4 table(s): ai_model_profile, ai_prompt_version,
#       eval_result, eval_run
#       http(s) literal hits: 0
#       （扫描的格子数随造了几趟运行而变，命中数恒为 0；只登记不造运行是 90 格）
# → [3] alias 'model.main' -> environment variable MODEL_ALIAS_MODEL_MAIN
#       unresolved without MODEL_ALIAS_MODEL_MAIN: ConfigError: model endpoint alias
#       'model.main' has no target configured: set MODEL_ALIAS_MODEL_MAIN to the real
#       base address for this deployment
#       resolved from MODEL_ALIAS_MODEL_MAIN -> https://gateway.example.invalid/v1 (never stored)
# → [4] ORM columns: created_at, created_by, deleted_at, endpoint_alias, id, name,
#       org_id, params, site_id, updated_at
# → all AC4 checks passed: only an alias is stored, the address stays in the environment
# → 退出码 0

# 反向验证：把库复制一份并往里塞一条地址，"扫描"这一步必须失败，说明它真在读数据而不是空跑
python scripts/verify_alias_only.py --plant-address build/demo/negative.sqlite3
# → [plant] copied … -> build/demo/negative.sqlite3 and wrote an address into …
python scripts/verify_alias_only.py build/demo/negative.sqlite3
# →     http(s) literal hits: 1
# → [fail] http(s) literals found: ai_model_profile.endpoint_alias=https://planted.example.invalid/v1
# → 退出码 1

# 三、别名换地址只在调用时从环境变量取
python -c "from app import config; print(config.alias_env_var('model.main'))"        # → MODEL_ALIAS_MODEL_MAIN
python -c "from app import config; print(config.resolve_model_endpoint('model.main'))"
# → app.config.ConfigError: model endpoint alias 'model.main' has no target configured:
#   set MODEL_ALIAS_MODEL_MAIN to the real base address for this deployment    （退出码 1）

# 四、写入口本身就拒绝地址
python -m app.cli_model_profile --name default --endpoint-alias https://gateway.example.invalid/v1
# → [error] endpoint_alias must be a plain alias such as model.main, not an address
# → 退出码 1（bash 里看 echo $?，PowerShell 里看 $LASTEXITCODE）

python -m pytest -q tests/test_model_profile.py tests/test_dependency_rules.py
# → 10 passed
```

### 3.5 一次性重跑全部证据

```bash
python scripts/capture_checks.py        # 重写 docs/checks/*.txt（pytest、cli-*、py-version、db-init）
python scripts/capture_api_examples.py  # 重写 docs/checks/api-examples.txt
python scripts/capture_checks_v2.py     # 重写 docs/checks/v2-*.txt 并逐条断言退出码
python scripts/capture_checks_v3.py     # 重写 docs/checks/v3-*.txt（AC3/AC4 两个可执行核对）
# 末两个的末行 → all captured commands matched their expectations，退出码 0
```

四个脚本都会自己建临时库、自己清理，跑完工作区只多出 `build/`（已在 `.gitignore` 里）。
生成的 `docs/checks/v3-ac3-query.txt` 与 `docs/checks/v3-ac4-verify.txt` 就是第 3.3、3.4 两节命令的真实输出。

---

## 4. 实测数字（对应 acceptance 的「including constraints and edge cases」）

### 4.1 pytest

```text
$ python -m pytest -q
54 passed
```

分布（`python -m pytest --collect-only -q`）：

| 文件 | 用例数 |
| --- | --- |
| `tests/test_prompt_register.py` | 17 |
| `tests/test_api_eval.py` | 11 |
| `tests/test_frontmatter.py` | 10 |
| `tests/test_dependency_rules.py` | 5 |
| `tests/test_eval_binding.py` | 5 |
| `tests/test_model_profile.py` | 5 |
| `tests/test_scenario_demo.py` | 1 |
| **合计** | **54**（7 个文件） |

单条 AC 的定向命令（本轮实测）：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q tests/test_prompt_register.py::test_content_change_without_version_bump_exits_1_and_writes_nothing` | `1 passed` |
| `python -m pytest -q tests/test_prompt_register.py::test_second_run_is_idempotent_and_reports_skipped` | `1 passed` |
| `python -m pytest -q tests/test_api_eval.py::test_history_uses_the_most_recent_run_not_the_first` | `1 passed` |
| `python -m pytest -q tests/test_model_profile.py::test_model_profile_rejects_an_address_as_alias tests/test_dependency_rules.py::test_no_hard_coded_network_addresses` | `2 passed` |
| `python -m pytest -q tests/test_model_profile.py tests/test_dependency_rules.py` | `10 passed` |
| `python -m pytest -q tests/test_api_eval.py` | `11 passed` |

### 4.2 replay_history 造出的三趟与 history 返回值

`python scripts/replay_history.py build/demo/run.sqlite3` 的真实输出（同一版本 `1.1.0`，三趟 `finished_at` 递增）：

| 第几趟 | 通过/总数 | rate |
| --- | --- | --- |
| 第 1 趟 | 3 / 5 | `0.6000` |
| 第 2 趟 | 9 / 10 | `0.9000` |
| 第 3 趟（最近） | 4 / 5 | `0.8000` |

`GET /api/eval/prompt-versions/member_service/faq_answer/history` 随即返回（`versions` 里每个已登记版本一行）：

```json
{
  "tool": "member_service",
  "prompt_key": "faq_answer",
  "org_id": "00000000-0000-0000-0000-000000000000",
  "versions": [
    {
      "version": "1.1.0",
      "created_at": "2026-11-24T06:52:44.015286",
      "latest_run_id": "（第三趟的真实 UUID，每次 replay 重新生成）",
      "latest_pass_rate": 0.8,
      "latest_run_finished_at": "2026-11-22T09:23:00"
    }
  ]
}
```

（报文逐字取自 `docs/checks/v3-ac3-query.txt`，只有 UUID 与 created_at 随运行而变。）

即 `latest_pass_rate` 是 **0.8**：既不是第一趟的 0.6，也不是三趟均值 0.7667。这就是 AC3 要断言的语义，
对应 `app/api_eval.py:87` 的排序键与 `app/api_eval.py:210` 的计算式。完整报文见 `docs/checks/v3-ac3-query.txt`。从未评测过的版本返回 `null` 而不是 `0.0`
（`app/schemas.py:22-29` 写明理由：`0` 会被读成「评测过且全错」，与事实相反）。

### 4.3 `ai_model_profile` 没有 url 列，怎么验证

三条互不依赖的验证，任一条成立即可；本仓库三条都做了：

1. **表结构**：`PRAGMA table_info(ai_model_profile)` 的列名单是
   `id,name,endpoint_alias,params,org_id,site_id,created_at,created_by,updated_at,deleted_at`，
   逐列比对没有 `url` / `base_url` / `endpoint_url` / `address`。同一结论由 `migrations/versions/0001_prompt_registry.py:65-83`
   的建表语句独立佐证（PG 上执行的是这条迁移）。
2. **数据内容**：把四张表（`ai_model_profile`、`ai_prompt_version`、`eval_run`、`eval_result`）的每一行、每一个字符串列
   都扫一遍 `http://` / `https://`，命中 **0** 条（`docs/checks/v2-ac4-alias-only.txt` 与 `docs/checks/v3-ac4-verify.txt` 都是 `http(s) literal hits: 0`）。
   同一脚本带反向验证：往库的副本里写一条地址后命中数变 1、退出码 1，说明它真在读数据。
3. **代码层面**：`tests/test_dependency_rules.py::test_no_hard_coded_network_addresses` 用 AST/文本扫描 `app/**/*.py`，
   出现地址字面量即失败；`app/cli_model_profile.py:73-79` 在写入前拒绝含 `/` 或 `:` 的别名——地址根本进不来。

### 4.4 退出码与行数一览

| 命令 | 关键输出 | 退出码 | 行数变化 |
| --- | --- | --- | --- |
| `python -m app.dbcheck init` | `schema ready in …` | 0 | 建四张表 |
| `prompt-register`（首次） | `inserted 5, skipped 0, total 5` | 0 | 0 → 5 |
| `prompt-register`（再次） | `inserted 0, skipped 5, total 5` | 0 | 5 → 5 |
| `prompt-register`（改正文不升版本） | `code=version_content_conflict`、`1 content error(s); nothing was written` | **1** | 5 → 5 |
| `prompt-register`（同一改动升到 1.2.0） | `inserted 1, skipped 4, total 5` | 0 | 5 → 6 |
| `model-profile`（正常） | `insert name=model.main alias=model.main …` | 0 | `ai_model_profile` 1 行 |
| `model-profile`（别名写成地址） | `endpoint_alias must be a plain alias …` | **1** | 0 行 |
| `replay_history.py` | 三趟 `rate=0.6000 / 0.9000 / 0.8000` | 0 | `eval_run` 3 行 |
| 缺 `DATABASE_URL` 跑 `prompt-register` | `environment variable DATABASE_URL is not set …` | **2** | 0 |
| `alembic upgrade head`（未设 `DATABASE_URL`） | `RuntimeError: DATABASE_URL is not set; alembic will not guess a database.` | **1** | — |
| `alembic upgrade head`（指向 PostgreSQL 但本机无驱动） | `DatabaseUnavailable: … No module named 'psycopg'` | **1** | — |
| `alembic upgrade head --sql`（离线渲染） | 83 行非空 DDL | 0 | — |

### 4.5 环境边界（如实声明）

- 上面所有数字在 **SQLite** 上产生。本机没有 PostgreSQL 服务、也没有安装 `psycopg`（`python -c "import psycopg"` →
  `ModuleNotFoundError`，`127.0.0.1:5432` 无监听），因此 **没有** 在真实 PostgreSQL 上实跑迁移与 JSONB 落盘。
- PostgreSQL 侧由 `.github/workflows/ci.yml` 的 `pgvector/pgvector:pg16` 服务容器覆盖（`migrate` → `prompt-register` →
  `eval-lint` → `pytest`）；本机只把迁移要执行的 DDL 用 `alembic upgrade head --sql` 渲染出来供核对。
- 未接 Redis、未接统一鉴权中间件（`org_id` 目前取请求头或 `EVAL_ORG_ID`），与 `README.md` 第 9 节「已知边界」一致。

---

## 5. 证据文件索引

| 文件 | 内容 |
| --- | --- |
| `docs/checks/pytest.txt` | `python -m pytest -q` 全量输出，`54 passed` |
| `docs/checks/cli-ok.txt` | 首次登记 5 个文件，退出码 0 |
| `docs/checks/cli-idempotent.txt` | 再跑一次，5 条 `[skip]`，行数不变，退出码 0 |
| `docs/checks/cli-fail-version.txt` | 改正文不升版本：`code=version_content_conflict`，退出码 1 |
| `docs/checks/cli-fail-rollback.txt` | 版本回退到 1.0.9，退出码 1 |
| `docs/checks/cli-fail-frontmatter.txt` | front-matter 字段拼错（`versoin`），带 `did you mean 'version'`，退出码 1 |
| `docs/checks/cli-exit2-unmigrated.txt` / `cli-exit2-unreachable.txt` | 环境类失败的退出码 2 |
| `docs/checks/cli-dry-run-no-db.txt` | 无库 + `--dry-run --allow-missing-db`，退出码 0 |
| `docs/checks/db-init.txt` | `dbcheck init` 建表输出 |
| `docs/checks/py-version.txt` | 解释器版本 |
| `docs/checks/api-examples.txt` | 两个查询接口的真实 JSON 报文（含 `null` 通过率、跨租户 404、评测模式回显） |
| `docs/checks/v2-environment.txt` | 8 个关键包容版本、`import psycopg` 失败、5432 无监听、清空环境变量后仍 `54 passed` |
| `docs/checks/v2-install.txt` | 三个 requirements 文件原文、`pip install`、alembic 四条路径 |
| `docs/checks/v2-pytest.txt` | 本轮 `python -m pytest -q` 输出 |
| `docs/checks/v2-test-inventory.txt` | 7 个测试文件、54 个用例的分布 |
| `docs/checks/v2-ac1-conflict.txt` | AC1：冲突退回，退出码 1，行数 5 → 5 |
| `docs/checks/v2-ac1-bumped-ok.txt` | AC1 对照：升到 1.2.0，插入 1 行，行数 5 → 6 |
| `docs/checks/v2-ac2-idempotent.txt` | AC2：首次 `inserted 5`、再次 `inserted 0, skipped 5`，行数 0 → 5 → 5 |
| `docs/checks/v2-ac3-history-runs.txt` | AC3：三趟运行的库内真值 |
| `docs/checks/v2-ac3-api-responses.txt` | AC3：两个接口的完整报文，含两个 404 |
| `docs/checks/v2-ac4-alias-only.txt` | AC4：列名、只存别名、全库零地址字面量、别名解析与失败 |
| `docs/checks/v3-ac3-query.txt` | AC3：`scripts/query_history.py` 的真实输出（history 200 / runs 200 / 跨租户 404 / 未知运行 404 / 退出码 0） |
| `docs/checks/v3-ac4-verify.txt` | AC4：`scripts/verify_alias_only.py` 的真实输出（四步全过，退出码 0） |
| `docs/DELIVERY-EVIDENCE.md` | 更长的验收项对照表与「补充证据 v2」一节 |
| `scripts/capture_checks.py` / `scripts/capture_api_examples.py` / `scripts/capture_checks_v2.py` | 上面全部证据的生成脚本 |
| `scripts/verify_alias_only.py` | AC4 的可执行核对：列名、全库地址字面量、别名只从环境变量解析、ORM 字段；带 `--plant-address` 反向验证 |
| `scripts/capture_checks_v3.py` | 重写上面两个 v3 证据文件，并断言退出码与关键行 |
| `scripts/query_history.py` | AC3 的可执行核对：用 TestClient 打两个查询接口并打印真实报文，跨租户与未知运行各断言 404 |


# 提示词文件头规范（Prompt front matter）

本文件是 `prompt-register` 命令与评估登记体系的**契约文档**：它描述 `prompts/` 目录里每个提示词文件的格式，
以及 `app/frontmatter.py` 实际执行的校验规则。文档与实现的对应关系在文末第 7 节逐条列出，
并由 `tests/test_frontmatter.py` 覆盖。**任何格式变更都必须同时修改本文件、`app/frontmatter.py` 与测试**，
三者不一致时以测试为准（测试即契约的可执行形式）。

## 1. 文件结构

每个提示词是一个 Markdown 文件（`.md`），内容由两部分组成：

1. **文件头（front matter）**：文件第一行必须是 `---`，中间是 YAML 映射，再以单独一行 `---` 结束；
2. **正文（body）**：第二个 `---` 之后的全部内容，就是真正发给模型的提示词文本。

```markdown
---
tool: member_service
prompt_key: faq_answer
version: 1.1.0
changelog: "1.1.0: 明确只依据知识库条目作答；1.0.0: 初版"
---

你是会员空间的在线答疑助手。

## 知识库片段
{{context}}

## 会员问题
{{question}}
```

约定：

- 文件必须是 **UTF-8** 编码（允许带 BOM，读取时会剥除）。非 UTF-8 文件报 `encoding` 错误。
- 文件头围栏必须是文件第一行（BOM 之外的第一个字符起），前导空行不被接受。
- 目录未做限制，`prompts/` 下任意层级的 `*.md` 都会被扫描（例如 `prompts/triage/intent_router.md`）；
  以 `.` 开头的目录/文件、以及 `*.md.txt`、`*.rst`、`*.pyc` 后缀会被跳过。

## 2. 字段

`app/frontmatter.py` 中 `REQUIRED_FIELDS` 与 `OPTIONAL_FIELDS` 即白名单，`ALLOWED_FIELDS` 为二者并集。
**白名单之外的任何字段都会直接报错，不会被静默忽略**（见第 4 节 `unknown_field`）。

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `tool` | 是 | 非空字符串 | 业务工具/服务名，例如 `member_service`、`billing_service`。不得包含 `/`。最大长度 32（数据库 `String(32)`）。 |
| `prompt_key` | 是 | 非空字符串 | 提示词在该工具内的标识，例如 `faq_answer`。不得包含 `/`。最大长度 64（数据库 `String(64)`）。 |
| `version` | 是 | 三段数字字符串 | 语义化版本 `MAJOR.MINOR.PATCH`，**必须写成字符串**。规则见第 3 节。 |
| `changelog` | 否 | 字符串 | 变更说明，入库到 `ai_prompt_version.changelog`。**不参与内容哈希**（见第 5 节）。 |

注意 `version: 1.0`（不加引号且只有两段）会被 YAML 解析成浮点数 `1.0`，此时报 `invalid_version` 并明确提示
版本必须是三段字符串——不会退化成 `invalid_field`，因为要修的是版本号本身。

## 3. 版本号规则（`version`）

正则（`app/frontmatter.py` 的 `SEMVER_PATTERN`）：

```
^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$
```

即：

- 恰好三段，用 `.` 分隔；
- 每段是十进制非负整数，**不允许前导零**（`1.09.0` 非法，应写 `1.9.0`）；
- 不允许 `v` 前缀、`-rc1`/`+build` 之类后缀（本单未要求预发布版本，出现即报 `invalid_version`）。

比较规则：`Semver.parse()` 解析成 `(major, minor, patch)` 整数元组后按元组比较，
**绝不做字符串比较**——字符串比较会认为 `"1.10.0" < "1.9.0"`，而正确结果是 `1.10.0 > 1.9.0`。
测试 `test_semver_compares_numerically_not_lexicographically` 与 `test_version_regression_uses_numeric_order`
分别从比较器与命令两个层面锁住这一点。

## 4. 错误码

每个错误都带 `code`，CLI 打印成 `code=<code> file=<相对路径>` 格式，便于 CI 里 grep 与分流处理。
**全部错误都退出码 1**（内容问题：只能改提示词文件来解决），与退出码 2（环境/数据库问题）严格区分。

| code | 触发条件 | 修复方式 |
| --- | --- | --- |
| `missing_frontmatter` | 文件第一行不是 `---` | 补文件头 |
| `unclosed_frontmatter` | 没有第二个 `---` 收尾 | 补收尾围栏 |
| `invalid_yaml` | 文件头不是合法 YAML，或不是映射（例如是一个列表） | 修正 YAML 语法 |
| `unknown_field` | 出现白名单之外的字段（例如把 `version` 拼成 `versoin`） | 改成正确字段名；报文会给出 `did you mean 'version' instead of 'versoin'?` |
| `missing_field` | 缺 `tool` / `prompt_key` / `version` | 补字段 |
| `invalid_field` | 字段存在但为空、不是字符串，或 `tool`/`prompt_key` 含 `/`；`changelog` 不是字符串 | 改成非空字符串 |
| `invalid_version` | 版本不是三段数字字符串（含写成浮点数的情况） | 改成 `MAJOR.MINOR.PATCH` |
| `empty_body` | 正文去掉首尾空白后为空 | 写真正的提示词正文 |
| `encoding` | 文件不是 UTF-8 | 转成 UTF-8 保存 |
| `duplicate_version` | 同一次扫描中两个文件声明了相同的 `(tool, prompt_key, version)` | 删除/改名其中一个文件 |
| `version_content_conflict` | 库里已有该版本，但内容哈希不同（改了正文没提版本） | **升版本号**，不要原地改已登记版本 |
| `version_regression` | 提交的版本低于该 `(tool, prompt_key)` 已登记的最高版本 | 用更高的版本号 |

## 5. 内容哈希（`content_sha256`）

哈希**只覆盖正文**，不包含文件头。实现（`app/frontmatter.py`）：

```python
def normalize_text(text: str) -> str:
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    return unified.strip()

def content_sha256(body: str) -> str:
    return hashlib.sha256(normalize_text(body).encode("utf-8")).hexdigest()
```

三条推论，且都有测试覆盖：

1. **改 `changelog` 不改变哈希**：文件头整体被排除，改说明文字不会触发“版本未提升”报错
   （`test_changelog_change_does_not_change_hash`、`test_editing_changelog_only_is_not_a_conflict`）。
2. **CRLF 与 LF 算同一哈希**：换行统一成 LF 再算，Windows 与 Linux 检出同一份正文得到同一个值
   （`test_crlf_and_lf_bodies_produce_the_same_hash`、`test_crlf_file_registers_with_the_same_hash_as_lf`）。
3. **首尾空白被去除**：正文末尾多一个换行不影响哈希（`test_trailing_whitespace_does_not_change_hash`）。

差异对比时 CLI 只打印哈希前 12 位（`58a3ed4f3f57... vs 193ef74e0b25...`）方便人眼比对，
库里存的是完整 64 位十六进制（`String(64)`）。

## 6. 幂等与批量原子性

- **幂等**：同 `(org_id, tool, prompt_key, version)` 且哈希相同 → 判为 `skip`，不插入新行，命令退出码 0。
  连跑两次的差异只有 `inserted 5, skipped 0` 变成 `inserted 0, skipped 5`，行数不变
  （证据：`docs/checks/cli-ok.txt` 与 `docs/checks/cli-idempotent.txt`）。
- **批量原子性**：整批先校验、再写入，写入在同一事务里一次性 commit。任一文件有问题则整批不落库，
  计划里的 `[insert]` 全部降级为 `[error] ...（not written: the batch has errors）`
  （证据：`docs/checks/cli-fail-frontmatter.txt`）。

## 7. 文档与实现的对应关系

| 文档小节 | 实现位置 | 测试 |
| --- | --- | --- |
| 第 1 节 文件结构 | `app/frontmatter.py:split_frontmatter`、`load_prompt_file`、`discover_prompt_files` | `test_frontmatter.py::test_parses_header_and_body`、`test_file_must_start_with_a_fence` |
| 第 2 节 字段白名单 | `REQUIRED_FIELDS` / `OPTIONAL_FIELDS` / `ALLOWED_FIELDS` in `app/frontmatter.py` | `test_unknown_field_is_rejected_with_a_hint`、`test_missing_required_field_is_rejected` |
| 第 3 节 版本号 | `SEMVER_PATTERN`、`Semver.parse`、`Semver.tuple` | `test_version_must_have_three_numeric_segments`、`test_semver_compares_numerically_not_lexicographically` |
| 第 4 节 错误码 | `FrontmatterError.code`、`RegistryError.code`、`registry.plan_actions` | `test_prompt_register.py` 中每个 `test_*_exits_1` 用例 |
| 第 5 节 内容哈希 | `normalize_text`、`content_sha256` | `test_changelog_change_does_not_change_hash`、`test_crlf_and_lf_bodies_produce_the_same_hash` |
| 第 6 节 幂等/原子性 | `registry.plan_registration`、`apply_registration`、`cli_prompt_register.main` | `test_second_run_is_idempotent`、`test_conflict_writes_nothing` |

## 8. 新增字段的流程

1. 在 `app/frontmatter.py` 的 `OPTIONAL_FIELDS`（或 `REQUIRED_FIELDS`）里加名字；
2. 在 `PromptFile` 与 `Parse` 逻辑里取用该字段，需要入库则在 `app/models.py` 与 Alembic revision 里加列；
3. 更新本文件第 2 节的表格；
4. 补 `tests/test_frontmatter.py` 用例（至少一条正例、一条类型错误反例）；
5. 跑 `python -m pytest -q`，全绿后再合入——`prompt-register` 在 CI 中位于 `eval-lint` 之前，
   字段变更若不同步更新文档与测试，会直接卡住后续评测步骤。

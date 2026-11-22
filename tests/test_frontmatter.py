"""Front matter parsing, hashing and semantic version ordering."""

from __future__ import annotations

import pytest

from app.frontmatter import (
    FrontmatterError,
    Semver,
    content_sha256,
    normalize_text,
    parse_prompt_text,
    split_frontmatter,
)

VALID = """---
tool: member_service
prompt_key: faq_answer
version: 1.0.0
changelog: initial
---

你是答疑助手。

## 问题
{{question}}
"""


def test_split_frontmatter_returns_header_and_body():
    header, body = split_frontmatter(VALID, path="faq.md")
    assert header["tool"] == "member_service"
    assert header["prompt_key"] == "faq_answer"
    assert header["version"] == "1.0.0"
    assert body.strip().startswith("你是答疑助手。")


def test_parse_prompt_text_accepts_valid_file():
    header, body = parse_prompt_text(VALID, path="faq.md")
    assert header["changelog"] == "initial"
    assert "{{question}}" in body


def test_missing_frontmatter_field_is_rejected():
    broken = VALID.replace("prompt_key: faq_answer\n", "")
    with pytest.raises(FrontmatterError) as excinfo:
        parse_prompt_text(broken, path="faq.md")
    assert excinfo.value.code == "missing_field"
    assert "prompt_key" in excinfo.value.message


def test_unknown_frontmatter_field_is_rejected_with_hint():
    typo = VALID.replace("version: 1.0.0", "versoin: 1.0.0")
    with pytest.raises(FrontmatterError) as excinfo:
        parse_prompt_text(typo, path="faq.md")
    assert excinfo.value.code in {"unknown_field", "missing_field"}
    assert "versoin" in excinfo.value.message


def test_version_must_have_three_numeric_segments():
    for bad in ["1.0", "1", "1.0.0.0", "v1.0.0", "1.0.x", ""]:
        with pytest.raises(FrontmatterError) as excinfo:
            Semver.parse(bad, path="faq.md")
        assert excinfo.value.code == "invalid_version"


def test_semver_compares_numerically_not_lexicographically():
    assert Semver.parse("1.10.0") > Semver.parse("1.9.0")
    assert Semver.parse("1.10.0").tuple == (1, 10, 0)
    assert sorted(
        [Semver.parse(v) for v in ("1.10.0", "1.9.0", "2.0.0", "1.2.10")],
        key=lambda item: item.tuple,
    ) == [Semver.parse("1.2.10"), Semver.parse("1.9.0"), Semver.parse("1.10.0"), Semver.parse("2.0.0")]


def test_crlf_and_lf_bodies_hash_identically():
    body = "line one\nline two\nline three"
    assert content_sha256(body) == content_sha256(body.replace("\n", "\r\n"))
    assert content_sha256(body) == content_sha256(body.replace("\n", "\r"))


def test_hash_ignores_frontmatter_and_trailing_whitespace():
    first = VALID
    second = VALID.replace("changelog: initial", "changelog: rewritten for the demo") + "\n\n"
    assert content_sha256(parse_prompt_text(first, path="a.md")[1]) == content_sha256(
        parse_prompt_text(second, path="a.md")[1]
    )


def test_body_change_changes_hash():
    first = parse_prompt_text(VALID, path="a.md")[1]
    second = parse_prompt_text(VALID.replace("你是答疑助手。", "你是客服助手。"), path="a.md")[1]
    assert content_sha256(first) != content_sha256(second)


def test_normalize_text_strips_outer_whitespace():
    assert normalize_text("\n\n  hello\r\nworld  \n\n") == "hello\nworld"

"""Prompt file front matter: parsing, validation and body hashing.

A prompt file is a Markdown file whose first line is `---`, followed by a YAML
mapping with the keys tool / prompt_key / version (required) and changelog
(optional), followed by a closing `---` line and the prompt body.
The content hash covers the body only: front matter edits (a changelog entry,
an author note) never change the hash, and CRLF is normalised to LF so the same
text written on Windows and on Linux hashes identically.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_FIELDS: tuple[str, ...] = ("tool", "prompt_key", "version")
OPTIONAL_FIELDS: tuple[str, ...] = ("changelog",)
ALLOWED_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + OPTIONAL_FIELDS

#: Semantic version: exactly three numeric segments, no leading zeros.
SEMVER_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
_SEMVER_RE = re.compile(SEMVER_PATTERN)

#: Suffixes that make a prompt file not a prompt (documentation sidecars).
_SKIP_SUFFIXES = (".md.txt", ".rst", ".pyc")


class FrontmatterError(ValueError):
    """A prompt file is malformed; the message names the offending file."""

    def __init__(self, message: str, *, path: str, code: str = "invalid_frontmatter") -> None:
        super().__init__(f"{path}: {message}")
        self.message = message
        self.path = path
        self.code = code


class Semver:
    """Three numeric segments; comparisons are numeric, never lexicographic."""

    __slots__ = ("major", "minor", "patch", "raw")

    def __init__(self, major: int, minor: int, patch: int) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch
        self.raw = f"{major}.{minor}.{patch}"

    @classmethod
    def parse(cls, raw: str, *, path: str = "<memory>") -> "Semver":
        if not isinstance(raw, str):
            raise FrontmatterError(
                f"version must be a string, got {type(raw).__name__}",
                path=path,
                code="invalid_version",
            )
        match = _SEMVER_RE.match(raw.strip())
        if match is None:
            raise FrontmatterError(
                f"version {raw!r} is not a semantic version with three numeric "
                "segments (expected MAJOR.MINOR.PATCH, e.g. 1.4.0)",
                path=path,
                code="invalid_version",
            )
        return cls(*(int(part) for part in match.groups()))

    @property
    def tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Semver):
            return NotImplemented
        return self.tuple == other.tuple

    def __lt__(self, other: "Semver") -> bool:
        return self.tuple < other.tuple

    def __le__(self, other: "Semver") -> bool:
        return self.tuple <= other.tuple

    def __gt__(self, other: "Semver") -> bool:
        return self.tuple > other.tuple

    def __ge__(self, other: "Semver") -> bool:
        return self.tuple >= other.tuple

    def __hash__(self) -> int:
        return hash(self.tuple)

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"Semver({self.raw!r})"


def normalize_text(text: str) -> str:
    """Normalise line endings to LF and strip leading/trailing whitespace."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    return unified.strip()


def content_sha256(body: str) -> str:
    """SHA-256 over the normalised body; front matter is never included."""
    return hashlib.sha256(normalize_text(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptFile:
    """A parsed prompt file, ready to be registered."""

    path: Path
    rel_path: str
    tool: str
    prompt_key: str
    version: str
    semver: Semver
    changelog: str | None
    body: str
    content_sha256: str


def split_frontmatter(text: str, *, path: str) -> tuple[dict, str]:
    """Split a `---` fenced YAML header from the Markdown body."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("\ufeff"):
        normalized = normalized[1:]
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError(
            "file must start with a `---` front matter fence",
            path=path,
            code="missing_frontmatter",
        )
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        raise FrontmatterError(
            "front matter is not closed by a second `---` line",
            path=path,
            code="unclosed_frontmatter",
        )
    header_text = "\n".join(lines[1:closing])
    body = "\n".join(lines[closing + 1 :])
    try:
        header = yaml.safe_load(header_text)
    except yaml.YAMLError as exc:
        raise FrontmatterError(
            f"front matter is not valid YAML: {exc}",
            path=path,
            code="invalid_yaml",
        ) from exc
    if header is None:
        header = {}
    if not isinstance(header, dict):
        raise FrontmatterError(
            f"front matter must be a YAML mapping, got {type(header).__name__}",
            path=path,
            code="invalid_yaml",
        )
    return header, body


def _unknown_field_message(unknown: list[str], header: dict) -> str:
    from difflib import get_close_matches

    parts = [
        "unknown front matter field(s): " + ", ".join(repr(name) for name in unknown),
        "allowed fields: " + ", ".join(ALLOWED_FIELDS),
    ]
    for name in unknown:
        hint = get_close_matches(name, ALLOWED_FIELDS, n=1, cutoff=0.6)
        if hint:
            parts.append(f"did you mean {hint[0]!r} instead of {name!r}?")
    return "; ".join(parts)


def parse_prompt_text(text: str, *, path: str) -> tuple[dict, str]:
    """Parse and validate a prompt file body; returns (front matter, body)."""
    header, body = split_frontmatter(text, path=path)
    unknown = [str(key) for key in header if str(key) not in ALLOWED_FIELDS]
    if unknown:
        raise FrontmatterError(
            _unknown_field_message(sorted(unknown), header),
            path=path,
            code="unknown_field",
        )
    missing = [name for name in REQUIRED_FIELDS if name not in header]
    if missing:
        raise FrontmatterError(
            "missing required front matter field(s): " + ", ".join(missing),
            path=path,
            code="missing_field",
        )
    for name in REQUIRED_FIELDS:
        value = header[name]
        if not isinstance(value, str) or not value.strip():
            # A version written as 1.0 is parsed by YAML as a float; report it as
            # a version problem so the operator fixes the number, not the type.
            code = "invalid_version" if name == "version" else "invalid_field"
            raise FrontmatterError(
                f"front matter field {name!r} must be a non-empty string, got "
                f"{type(value).__name__} {value!r}",
                path=path,
                code=code,
            )
    changelog = header.get("changelog")
    if changelog is not None and not isinstance(changelog, str):
        raise FrontmatterError(
            "front matter field 'changelog' must be a string when present",
            path=path,
            code="invalid_field",
        )
    return header, body


def load_prompt_file(path: Path, *, root: Path) -> PromptFile:
    """Read one prompt file from disk and validate it."""
    rel = path.relative_to(root).as_posix()
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FrontmatterError(
            f"file is not valid UTF-8: {exc}",
            path=rel,
            code="encoding",
        ) from exc
    header, body = parse_prompt_text(raw, path=rel)
    tool = header["tool"].strip()
    prompt_key = header["prompt_key"].strip()
    version_raw = header["version"].strip()
    semver = Semver.parse(version_raw, path=rel)
    if "/" in tool or "/" in prompt_key:
        raise FrontmatterError(
            "tool and prompt_key must not contain a slash",
            path=rel,
            code="invalid_field",
        )
    if not normalize_text(body):
        raise FrontmatterError(
            "prompt body is empty",
            path=rel,
            code="empty_body",
        )
    return PromptFile(
        path=path,
        rel_path=rel,
        tool=tool,
        prompt_key=prompt_key,
        version=semver.raw,
        semver=semver,
        changelog=header.get("changelog"),
        body=normalize_text(body),
        content_sha256=content_sha256(body),
    )


def discover_prompt_files(root: Path) -> list[Path]:
    """Every .md file under root, in a deterministic order, dotfiles skipped."""
    if not root.exists():
        raise FileNotFoundError(f"prompt directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"prompt path is not a directory: {root}")
    found: list[Path] = []
    for candidate in sorted(root.rglob("*.md")):
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if candidate.name.endswith(_SKIP_SUFFIXES):
            continue
        found.append(candidate)
    return found

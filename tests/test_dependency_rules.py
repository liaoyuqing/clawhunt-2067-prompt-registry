"""Static guarantees about what this application may import.

These are not style checks: the delivery contract forbids hard-coded network
addresses and third-party SDKs that ship data abroad, so the source is scanned.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN_PREFIXES = (
    "requests",
    "httpx",
    "urllib3",
    "aiohttp",
    "openai",
    "anthropic",
    "google.generativeai",
    "vertexai",
    "boto3",
    "botocore",
    "cohere",
    "groq",
    "mistralai",
    "dashscope",
    "zhipuai",
    "azure.ai",
    "langchain",
    "litellm",
    "replicate",
)


def python_sources() -> list[Path]:
    return sorted(path for path in APP_DIR.rglob("*.py"))


def test_app_package_has_sources():
    assert python_sources(), "the app package must contain Python modules"


def test_no_hard_coded_network_addresses():
    offenders = []
    for path in python_sources():
        text = path.read_text(encoding="utf-8")
        for scheme in ("http://", "https://"):
            if scheme in text:
                line = text[: text.index(scheme)].count("\n") + 1
                offenders.append(f"{path.name}:{line} contains {scheme}")
    assert offenders == [], offenders


def test_no_third_party_egress_sdk_is_imported():
    offenders = []
    for path in python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for prefix in FORBIDDEN_PREFIXES:
                    if name == prefix or name.startswith(prefix + "."):
                        offenders.append(f"{path.name}: imports {name}")
    assert offenders == [], offenders


def test_no_credentials_or_dsns_literals():
    needles = ("postgresql://", "postgresql+psycopg://", "redis://", "password=", "api_key=")
    offenders = []
    for path in python_sources():
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path.name}: contains {needle}")
    assert offenders == [], offenders


def test_config_reads_configuration_from_environment_only():
    text = (APP_DIR / "config.py").read_text(encoding="utf-8")
    assert "os.environ" in text
    assert "DEFAULT_ORG_ID" in text

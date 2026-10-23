#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""CI guardrail: model calls go through the gateway modules.

Every model call is meant to leave one content-free ``llm_call`` usage
record (model, feature, latency, token counts including cached and reasoning
tokens), go through the shared retry engine, and use a client built in one
place. A call made straight on an SDK client does none of that, and a cost
report built on the records silently under-counts it.

So only the gateway modules and the client factory may build an SDK client
or call the model API:

    genai.Client(...), AnthropicVertex(...), Anthropic(...)
    boto3.client("bedrock-runtime")
    .generate_content(), .generate_content_stream(), .embed_content()
    .converse(), .converse_stream(), .invoke_model(), .invoke_model_with_response_stream()

Anywhere else under ``backend/app`` is a violation. Call a gateway instead:
``structured_llm_gateway`` for schema-constrained JSON, ``text_llm_gateway``
for text or a caller-supplied schema object, ``chat_llm_gateway`` /
``bedrock_chat_llm_gateway`` for streaming chat, ``agent_llm_gateway`` for
tool loops.

Usage:  python3 scripts/check_llm_single_path.py [--root PATH]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_DIR = "backend/app"

# Modules allowed to talk to a model SDK, relative to the scan root's parent
# (``backend/app/...``). Keep this list short and every entry a gateway.
ALLOWED: frozenset[str] = frozenset(
    {
        "backend/app/services/vertex_client.py",
        "backend/app/services/structured_llm_gateway.py",
        "backend/app/services/bedrock_structured_llm_gateway.py",
        "backend/app/services/hedged_structured_llm_gateway.py",
        "backend/app/services/chat_llm_gateway.py",
        "backend/app/services/bedrock_chat_llm_gateway.py",
        "backend/app/services/agent_llm_gateway.py",
        "backend/app/services/text_llm_gateway.py",
        "backend/app/services/embedding_service.py",
    }
)

CALL_METHODS: frozenset[str] = frozenset(
    {
        "generate_content",
        "generate_content_stream",
        "embed_content",
        "converse",
        "converse_stream",
        "invoke_model",
        "invoke_model_with_response_stream",
    }
)
CLIENT_CONSTRUCTORS: frozenset[str] = frozenset({"Anthropic", "AsyncAnthropic", "AnthropicVertex"})


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _violation(call: ast.Call) -> str | None:
    func = call.func
    name = _dotted(func)
    last = name.rsplit(".", 1)[-1]
    if isinstance(func, ast.Attribute) and last in CALL_METHODS:
        return f"{name}()"
    if name.endswith("genai.Client"):
        return f"{name}()"
    if last in CLIENT_CONSTRUCTORS:
        return f"{name}()"
    if (name.endswith("boto3.client") or name.endswith("session.client")) and call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and first.value in {"bedrock-runtime", "bedrock"}:
            return f'{name}("{first.value}")'
    return None


def scan_source(source: str, filename: str) -> list[tuple[int, str]]:
    """Violations in one source text, as ``(line, what)``."""
    tree = ast.parse(source, filename=filename)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (what := _violation(node)) is not None:
            found.append((node.lineno, what))
    return sorted(found)


def scan(root: Path, scan_dir: str = DEFAULT_SCAN_DIR) -> list[str]:
    problems: list[str] = []
    for path in sorted((root / scan_dir).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWED:
            continue
        for line, what in scan_source(path.read_text(encoding="utf-8"), rel):
            problems.append(f"{rel}:{line}: {what} outside the LLM gateways")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    problems = scan(args.root)
    if problems:
        print(
            "Model calls must go through the LLM gateways (see scripts/check_llm_single_path.py):"
        )
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("ok: no direct model SDK use outside the LLM gateways")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Selftest for check_llm_single_path.py: it must flag each way of reaching a
model SDK from outside the gateways, and pass a gateway-only tree.

Run:  python3 scripts/check_llm_single_path_selftest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_llm_single_path as check

BAD_SNIPPETS = {
    "genai client": "from google import genai\nc = genai.Client(vertexai=True)\n",
    "generate_content": "r = client.models.generate_content(model='m', contents='x')\n",
    "stream": "s = client.aio.models.generate_content_stream(model='m')\n",
    "embed_content": "e = client.models.embed_content(model='m', contents=['x'])\n",
    "converse": "r = rt.converse(modelId='m')\n",
    "converse_stream": "r = rt.converse_stream(modelId='m')\n",
    "invoke_model": "r = rt.invoke_model(modelId='m', body='{}')\n",
    "anthropic client": "import anthropic\nc = anthropic.Anthropic()\n",
    "anthropic vertex": "from anthropic import AnthropicVertex\nc = AnthropicVertex()\n",
    "bedrock client": "import boto3\nc = boto3.client('bedrock-runtime')\n",
}

GOOD_SNIPPETS = {
    "gateway call": "from app.services.text_llm_gateway import generate_text as g\nt = g()\n",
    "unrelated client": "import boto3\nc = boto3.client('s3')\n",
    "mention in docstring": '"""Calls client.models.generate_content() elsewhere."""\nx = 1\n',
    "twilio messages.create": "m = twilio.messages.create(to='x')\n",
}


def main() -> int:
    failures: list[str] = []
    for label, src in BAD_SNIPPETS.items():
        if not check.scan_source(src, "x.py"):
            failures.append(f"missed: {label}")
    for label, src in GOOD_SNIPPETS.items():
        if check.scan_source(src, "x.py"):
            failures.append(f"false positive: {label}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        services = root / "backend/app/services"
        services.mkdir(parents=True)
        (services / "text_llm_gateway.py").write_text(BAD_SNIPPETS["generate_content"])
        (services / "elsewhere.py").write_text(BAD_SNIPPETS["generate_content"])
        problems = check.scan(root)
        if len(problems) != 1 or "elsewhere.py" not in problems[0]:
            failures.append(f"allowlist wrong: {problems}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("ok: check_llm_single_path flags every bypass and spares the gateways")
    return 0


if __name__ == "__main__":
    sys.exit(main())

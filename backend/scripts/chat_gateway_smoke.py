# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Local smoke test for GeminiChatLLMGateway against real Vertex AI.

Bypasses the chat route + DB + Firebase / MFA / playwright machinery —
calls the gateway directly so empty-response and prefix-handling bugs
surface in seconds instead of minutes.

Usage::

    cd backend
    # ADC must be live (gcloud auth application-default login) and the
    # active project must have Vertex AI enabled. Defaults below match
    # the dev project.
    GOOGLE_CLOUD_PROJECT=pablohealth-dev \\
    VERTEX_REGION=us-central1 \\
    GOOGLE_GENAI_USE_VERTEXAI=true \\
        poetry run python scripts/chat_gateway_smoke.py \\
        --model gemini-3.5-flash --prompt "What is hypertension?"

    # Reproduce the dev-env bug (google: prefix):
    poetry run python scripts/chat_gateway_smoke.py \\
        --model google:gemini-3.5-flash --prompt "What is hypertension?"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.chat_llm_gateway import GeminiChatLLMGateway


async def _run(model: str, prompt: str, max_tokens: int) -> int:
    gateway = GeminiChatLLMGateway()
    events: list[str] = []
    deltas: list[str] = []
    error_event: tuple[str | None, str | None] | None = None
    finish_reason: str | None = None

    print(f"--- calling stream_completion(model={model!r}) ---")
    async for ev in gateway.stream_completion(
        model=model,
        system_prompt="You are a helpful clinical assistant. Be concise.",
        prior_turns=[],
        new_user_text=prompt,
        max_output_tokens=max_tokens,
    ):
        if ev.delta is not None:
            deltas.append(ev.delta)
            events.append("delta")
            print(ev.delta, end="", flush=True)
        elif ev.finish_reason is not None:
            finish_reason = ev.finish_reason
            events.append(f"finish:{ev.finish_reason}")
            if ev.finish_reason == "error":
                error_event = (ev.error_code, ev.error_message)

    print()
    print("--- summary ---")
    print(f"events:         {','.join(events)}")
    print(f"delta_count:    {len(deltas)}")
    print(f"text_chars:     {sum(len(d) for d in deltas)}")
    print(f"finish_reason:  {finish_reason}")
    if error_event is not None:
        print(f"error_code:     {error_event[0]}")
        print(f"error_message:  {error_event[1]}")

    return 0 if (finish_reason == "stop" and deltas) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--prompt", default="In one short sentence, what is hypertension?")
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()

    print(f"GOOGLE_CLOUD_PROJECT={os.environ.get('GOOGLE_CLOUD_PROJECT')}")
    print(f"VERTEX_REGION={os.environ.get('VERTEX_REGION')}")
    print(f"GOOGLE_GENAI_USE_VERTEXAI={os.environ.get('GOOGLE_GENAI_USE_VERTEXAI')}")
    return asyncio.run(_run(args.model, args.prompt, args.max_tokens))


if __name__ == "__main__":
    raise SystemExit(main())

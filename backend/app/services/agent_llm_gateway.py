"""Agent gateway: a tool-using LLM loop that ends in one structured object.

The model calls caller-supplied tools to gather context over several turns,
then emits a single object constrained to a response schema. Generic by design:
callers own the tools (name, description, parameters, handler), the prompts, and
the schema; the gateway owns the loop and the final structured emit.

Sync, matching the other gateways — callers run from sync service code and an
async gateway would force ``asyncio.run`` gymnastics for no benefit.

Logging hygiene: prompts, tool arguments, and tool results may carry sensitive
content. Nothing here logs or embeds them — log lines and telemetry spans carry
counts, tool names, and the model name only.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .llm_json import extract_json_object
from .llm_provider import strip_provider_prefix
from .llm_telemetry import LLMSpanRequest, llm_span, usage_tokens
from .structured_llm_gateway import _to_gemini_schema
from .vertex_client import vertex_genai_client

logger = logging.getLogger(__name__)

# A tool handler takes the model-supplied arguments and returns a result string
# that is fed back into the conversation. Handlers own their own side-effect and
# logging hygiene (e.g. read-only sources, no sensitive content in their logs).
ToolHandler = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ToolSpec:
    """One tool the agent may call. ``parameters`` is a JSON-schema object."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation the model requested during a run."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentResult:
    """Outcome of one agent run.

    ``data`` is the parsed JSON object from the final structured emit (an empty
    dict if the emit could not be parsed — callers degrade defensively).
    ``tool_calls`` is the ordered list of invocations (count and names are safe
    to log; ``arguments`` are not). ``hit_step_limit`` is True if the loop was
    cut off at ``max_steps`` before the model stopped calling tools — a signal
    the result rests on incomplete context.
    """

    data: dict[str, Any]
    tool_calls: list[ToolCall] = field(default_factory=list)
    hit_step_limit: bool = False


class AgentLLMGateway(ABC):
    """Abstract tool-using-loop gateway that ends in one structured object."""

    @abstractmethod
    def run_agent(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ToolSpec],
        response_schema: dict[str, Any],
        max_steps: int = 8,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> AgentResult:
        """Run the gather-then-emit loop.

        Calls the model with ``tools`` available; dispatches each requested tool
        through its handler and feeds the result back, up to ``max_steps``
        rounds; then makes one final tool-free call constrained to
        ``response_schema`` and returns the parsed object.

        Raises:
            RuntimeError: transport / auth / SDK failure.
        """


def _function_calls(response: Any) -> list[Any]:
    """Extract function-call parts from a genai response, defensively."""
    calls: list[Any] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            call = getattr(part, "function_call", None)
            if call is not None and getattr(call, "name", None):
                calls.append(call)
    return calls


class GeminiAgentLLMGateway(AgentLLMGateway):
    """Production gateway: Vertex ``google.genai`` gather-then-emit loop.

    Phase 1 (gather): ``generate_content`` with the tools declared; while the
    model returns function calls, dispatch them and feed the responses back.
    Phase 2 (emit): one tool-free call with ``response_mime_type=application/json``
    and ``response_schema`` — the same shape as :class:`GeminiStructuredLLMGateway`
    — to extract the verdict. The whole run is recorded as one ``agent`` span.
    """

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = vertex_genai_client()
        return self._client

    def run_agent(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ToolSpec],
        response_schema: dict[str, Any],
        max_steps: int = 8,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> AgentResult:
        from google.genai import types

        client = self._get_client()
        normalized = strip_provider_prefix(model)
        by_name = {t.name: t for t in tools}
        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=_to_gemini_schema(types, t.parameters),
            )
            for t in tools
        ]
        # Annotated broad to satisfy the SDK's union-typed ``tools`` param
        # (list is invariant, so a bare ``list[Tool]`` would not assign).
        genai_tools: list[Any] | None = (
            [types.Tool(function_declarations=declarations)] if declarations else None
        )

        contents: list[Any] = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
        gather_config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=genai_tools,
        )

        invocations: list[ToolCall] = []
        hit_step_limit = False
        with llm_span(LLMSpanRequest(operation="agent", model=normalized)) as span:
            prompt_total = output_total = grand_total = 0
            for step in range(max_steps):
                response = self._generate(client, normalized, contents, gather_config)
                p, o, t = usage_tokens(getattr(response, "usage_metadata", None))
                prompt_total += p or 0
                output_total += o or 0
                grand_total += t or 0
                calls = _function_calls(response)
                if not calls:
                    break
                # Echo the model's tool-calling turn, then answer every call so
                # the next turn sees the results.
                contents.append(response.candidates[0].content)
                response_parts = []
                for call in calls:
                    args = dict(call.args or {})
                    invocations.append(ToolCall(name=call.name, arguments=args))
                    spec = by_name.get(call.name)
                    result = (
                        spec.handler(args)
                        if spec is not None
                        else f"error: unknown tool {call.name!r}"
                    )
                    response_parts.append(
                        types.Part.from_function_response(
                            name=call.name, response={"result": result}
                        )
                    )
                contents.append(types.Content(role="user", parts=response_parts))
                if step == max_steps - 1:
                    hit_step_limit = True

            data, emit_p, emit_o, emit_t = self._emit_structured(
                client,
                normalized,
                contents,
                system_prompt=system_prompt,
                response_schema=response_schema,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
            span.set_token_usage(
                prompt_tokens=prompt_total + emit_p,
                completion_tokens=output_total + emit_o,
                total_tokens=grand_total + emit_t,
            )

        logger.info(
            "agent run done: tool_calls=%d hit_step_limit=%s tools=%s",
            len(invocations),
            hit_step_limit,
            ",".join(sorted({c.name for c in invocations})) or "-",
        )
        return AgentResult(data=data, tool_calls=invocations, hit_step_limit=hit_step_limit)

    def _emit_structured(
        self,
        client: Any,
        model: str,
        contents: list[Any],
        *,
        system_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float,
    ) -> tuple[dict[str, Any], int, int, int]:
        """Final tool-free call constrained to the schema; returns (data, p, o, t)."""
        from google.genai import types

        emit_contents = [
            *contents,
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        text="Now emit your final answer as a single JSON object "
                        "matching the required schema."
                    )
                ],
            ),
        ]
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_schema=_to_gemini_schema(types, response_schema),
        )
        response = self._generate(client, model, emit_contents, config)
        p, o, t = usage_tokens(getattr(response, "usage_metadata", None))
        text = getattr(response, "text", "") or ""
        data = extract_json_object(text)
        if data is None:
            # Never log the model text. The caller degrades on an empty object.
            logger.warning("agent final emit had no JSON object; returning empty object")
            data = {}
        return data, p or 0, o or 0, t or 0

    @staticmethod
    def _generate(client: Any, model: str, contents: list[Any], config: Any) -> Any:
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            logger.exception("agent generate_content failed")
            raise RuntimeError(f"Agent LLM call failed: {exc}") from exc


@dataclass
class FakeAgentLLMGateway(AgentLLMGateway):
    """Deterministic gateway for tests — exercises the dispatch loop, no network.

    ``script`` is the ordered list of tool calls the "model" requests before
    emitting; each runs the real handler (so tool wiring is under test).
    ``final`` is the JSON object the structured emit returns. With an empty
    script the agent emits immediately (the zero-tool path). Recorded
    invocations are exposed on the returned :class:`AgentResult`.
    """

    final: dict[str, Any] = field(default_factory=dict)
    script: list[ToolCall] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run_agent(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ToolSpec],
        response_schema: dict[str, Any],
        max_steps: int = 8,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> AgentResult:
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            }
        )
        by_name = {t.name: t for t in tools}
        invocations: list[ToolCall] = []
        hit_step_limit = False
        for i, call in enumerate(self.script):
            if i >= max_steps:
                hit_step_limit = True
                break
            invocations.append(call)
            spec = by_name.get(call.name)
            if spec is None:
                raise KeyError(f"FakeAgentLLMGateway: scripted unknown tool {call.name!r}")
            spec.handler(dict(call.arguments))  # run for side effects / wiring
        return AgentResult(
            data=dict(self.final), tool_calls=invocations, hit_step_limit=hit_step_limit
        )


# Process-wide singleton, consistent with the sibling gateways. The gateway has
# no per-request state; the underlying ``google.genai`` client is lazily built.
_default_gateway_holder: list[AgentLLMGateway] = []


def get_default_agent_llm_gateway() -> AgentLLMGateway:
    """Return the process-wide :class:`GeminiAgentLLMGateway`."""
    if not _default_gateway_holder:
        _default_gateway_holder.append(GeminiAgentLLMGateway())
    return _default_gateway_holder[0]


__all__ = [
    "AgentLLMGateway",
    "AgentResult",
    "FakeAgentLLMGateway",
    "GeminiAgentLLMGateway",
    "ToolCall",
    "ToolSpec",
    "get_default_agent_llm_gateway",
]

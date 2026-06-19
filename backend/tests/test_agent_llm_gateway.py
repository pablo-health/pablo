"""Unit tests for the agent gateway's loop and pure helpers.

Network-free: the production Gemini path is not exercised here. The Fake
gateway drives the tool-dispatch loop, and the helpers are tested directly.
"""

from __future__ import annotations

import pytest

from backend.app.services.agent_llm_gateway import (
    AgentResult,
    FakeAgentLLMGateway,
    ToolCall,
    ToolSpec,
    _function_calls,
    get_default_agent_llm_gateway,
)

_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def _echo_tool(recorder: list[dict[str, object]]) -> ToolSpec:
    def handler(args: dict[str, object]) -> str:
        recorder.append(args)
        return "ok"

    return ToolSpec(
        name="lookup",
        description="Look something up.",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        handler=handler,
    )


class TestFakeAgentLLMGateway:
    def test_zero_tool_path_emits_immediately(self) -> None:
        gw = FakeAgentLLMGateway(final={"answer": "42"})
        result = gw.run_agent(
            model="google:gemini-x",
            system_prompt="sys",
            user_prompt="hi",
            tools=[],
            response_schema=_SCHEMA,
        )
        assert isinstance(result, AgentResult)
        assert result.data == {"answer": "42"}
        assert result.tool_calls == []
        assert result.hit_step_limit is False

    def test_scripted_tools_run_their_handlers(self) -> None:
        seen: list[dict[str, object]] = []
        gw = FakeAgentLLMGateway(
            final={"answer": "done"},
            script=[ToolCall("lookup", {"q": "a"}), ToolCall("lookup", {"q": "b"})],
        )
        result = gw.run_agent(
            model="google:gemini-x",
            system_prompt="sys",
            user_prompt="hi",
            tools=[_echo_tool(seen)],
            response_schema=_SCHEMA,
        )
        assert seen == [{"q": "a"}, {"q": "b"}]
        assert [c.name for c in result.tool_calls] == ["lookup", "lookup"]

    def test_unknown_scripted_tool_raises(self) -> None:
        gw = FakeAgentLLMGateway(final={}, script=[ToolCall("nope", {})])
        with pytest.raises(KeyError):
            gw.run_agent(
                model="m",
                system_prompt="s",
                user_prompt="u",
                tools=[_echo_tool([])],
                response_schema=_SCHEMA,
            )

    def test_step_limit_truncates_the_script(self) -> None:
        seen: list[dict[str, object]] = []
        gw = FakeAgentLLMGateway(
            final={"answer": "x"},
            script=[ToolCall("lookup", {"q": str(i)}) for i in range(5)],
        )
        result = gw.run_agent(
            model="m",
            system_prompt="s",
            user_prompt="u",
            tools=[_echo_tool(seen)],
            response_schema=_SCHEMA,
            max_steps=2,
        )
        assert len(seen) == 2
        assert result.hit_step_limit is True


class TestHelpers:
    def test_function_calls_is_defensive_on_empty_response(self) -> None:
        assert _function_calls(object()) == []
        assert _function_calls(None) == []


def test_default_gateway_is_singleton() -> None:
    assert get_default_agent_llm_gateway() is get_default_agent_llm_gateway()

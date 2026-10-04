# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The Bedrock structured-output gateway, against a stubbed Bedrock client.

Nothing here reaches AWS: the bedrock-runtime client is a stand-in that
records requests and replays one outcome, and the STS exchange is answered
by botocore's own Stubber.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import app.services.structured_llm_gateway as structured
import botocore.session
import httpx
import pytest
from app.reliability import SINGLE_ATTEMPT, RetryExhaustedError
from app.reliability.classify import is_pre_dispatch, is_transient
from app.reliability.hedge import INTERACTIVE_STALL_AFTER, FailureKind
from app.reliability.retry import LLM_REQUEST
from app.services.availability_parse_service import AvailabilityRuleParseService
from app.services.bedrock_structured_llm_gateway import (
    BedrockCallError,
    BedrockStructuredLLMGateway,
    build_botocore_session,
    to_json_schema,
)
from app.services.hedged_structured_llm_gateway import (
    HedgedStructuredLLMGateway,
    classify_structured_failure,
)
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    GeminiStructuredLLMGateway,
    StructuredCompletion,
    StructuredOutputTruncatedError,
    resolve_structured_llm_gateway,
)
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)
from botocore.stub import Stubber
from evals.availability_parse import run as parse_eval
from evals.availability_parse.cases import all_cases
from evals.llm_routing.providers import Script, error_504, ok, stall
from evals.llm_routing.scenarios import PRIMARY, SECONDARY, interactive_policy
from evals.llm_routing.sim import simulate_script
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

if TYPE_CHECKING:
    from collections.abc import Iterator

HAIKU = "bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_type": {"type": "string"},
                    "max": {"type": "integer", "nullable": True},
                },
                "required": ["rule_type"],
            },
        },
        "refusal_reason": {"type": "string", "nullable": True, "enum": ["ambiguous", "other"]},
        "date_intent": {"type": "object", "nullable": True, "properties": {}},
    },
    "required": ["proposals"],
}


def _response(
    tool_input: Any = None,
    *,
    stop_reason: str = "tool_use",
    name: str = "emit_structured_output",
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if tool_input is not None:
        content.append({"toolUse": {"toolUseId": "t1", "name": name, "input": tool_input}})
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": 120, "outputTokens": 9, "totalTokens": 129},
    }


class _StubClient:
    """Stands in for a bedrock-runtime client: records requests, replays outcomes."""

    def __init__(self, *outcomes: dict[str, Any] | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _gateway(
    *outcomes: dict[str, Any] | Exception,
) -> tuple[BedrockStructuredLLMGateway, _StubClient]:
    client = _StubClient(*outcomes)
    return BedrockStructuredLLMGateway(client_factory=lambda _timeout: client), client


def _call(gw: BedrockStructuredLLMGateway, **overrides: Any) -> StructuredCompletion:
    kwargs: dict[str, Any] = {
        "model": HAIKU,
        "system_prompt": "SYSTEM",
        "user_prompt": "9 to 5",
        "response_schema": _SCHEMA,
        "max_output_tokens": 256,
        "temperature": 0.0,
        "thinking_budget": 0,
        "retry_policy": SINGLE_ATTEMPT,
    }
    return gw.complete_structured(**(kwargs | overrides))


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "request quoted: SECRET-marker"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "Converse",
    )


class TestSchemaTranslation:
    def test_nullable_becomes_a_null_type(self) -> None:
        out = to_json_schema(_SCHEMA)
        item = out["properties"]["proposals"]["items"]
        assert item["properties"]["max"] == {"type": ["integer", "null"]}
        assert item["properties"]["rule_type"] == {"type": "string"}
        assert out["properties"]["date_intent"]["type"] == ["object", "null"]

    def test_nullable_enum_admits_null(self) -> None:
        refusal = to_json_schema(_SCHEMA)["properties"]["refusal_reason"]
        assert refusal == {"type": ["string", "null"], "enum": ["ambiguous", "other", None]}

    def test_no_nullable_keyword_survives_and_input_is_untouched(self) -> None:
        out = to_json_schema(_SCHEMA)
        assert "nullable" not in repr(out)
        assert _SCHEMA["properties"]["refusal_reason"]["nullable"] is True
        assert out["required"] == ["proposals"]


class TestSuccess:
    def test_forced_tool_use_is_parsed_into_the_result(self) -> None:
        gw, _ = _gateway(_response({"proposals": [{"rule_type": "working_hours"}]}))
        result = _call(gw)
        assert result.data == {"proposals": [{"rule_type": "working_hours"}]}
        assert result.output_tokens == 9
        assert result.finish_reason == "stop"

    def test_request_strips_prefix_and_forces_the_translated_tool(self) -> None:
        gw, client = _gateway(_response({"proposals": []}))
        _call(gw)
        sent = client.requests[0]
        assert sent["modelId"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        assert sent["system"] == [{"text": "SYSTEM"}]
        assert sent["messages"] == [{"role": "user", "content": [{"text": "9 to 5"}]}]
        assert sent["inferenceConfig"] == {"maxTokens": 256, "temperature": 0.0}
        spec = sent["toolConfig"]["tools"][0]["toolSpec"]
        assert spec["inputSchema"] == {"json": to_json_schema(_SCHEMA)}
        assert sent["toolConfig"]["toolChoice"] == {"tool": {"name": spec["name"]}}

    def test_guardrail_is_reported_as_safety(self) -> None:
        gw, _ = _gateway(_response({"proposals": []}, stop_reason="guardrail_intervened"))
        assert _call(gw).finish_reason == "safety"


class TestMalformedOutput:
    def test_truncation_raises_its_own_error(self) -> None:
        gw, _ = _gateway(_response({"proposals": [{"rule_"}]}, stop_reason="max_tokens"))
        with pytest.raises(StructuredOutputTruncatedError):
            _call(gw)

    def test_missing_tool_call_is_an_unusable_answer(self) -> None:
        gw, _ = _gateway(_response(None, stop_reason="end_turn"))
        with pytest.raises(ValueError, match="no 'emit_structured_output' tool call") as caught:
            _call(gw)
        assert classify_structured_failure(caught.value) is FailureKind.INVALID

    def test_a_different_tool_name_is_not_taken_as_the_answer(self) -> None:
        gw, _ = _gateway(_response({"proposals": []}, name="something_else"))
        with pytest.raises(ValueError, match="no 'emit_structured_output'"):
            _call(gw)

    def test_non_object_tool_input_is_an_unusable_answer(self) -> None:
        gw, _ = _gateway(_response(["not", "an", "object"]))
        with pytest.raises(ValueError, match="not an object"):
            _call(gw)


class TestTransientErrors:
    @pytest.mark.parametrize(
        "exc",
        [
            ReadTimeoutError(endpoint_url="https://bedrock-runtime"),
            ConnectTimeoutError(endpoint_url="https://bedrock-runtime"),
            EndpointConnectionError(endpoint_url="https://bedrock-runtime"),
            _client_error("ThrottlingException", 429),
            _client_error("ServiceUnavailableException", 503),
            _client_error("InternalServerException", 500),
            _client_error("ModelTimeoutException", 408),
        ],
        ids=[
            "read-timeout",
            "connect-timeout",
            "unreachable",
            "429",
            "503",
            "500",
            "model-timeout",
        ],
    )
    def test_a_single_attempt_fails_as_transient(self, exc: Exception) -> None:
        gw, client = _gateway(exc)
        with pytest.raises(RuntimeError, match="Structured LLM call failed") as caught:
            _call(gw, timeout_seconds=15.0)
        # One attempt only: what happens next is the hedged run's decision.
        assert len(client.requests) == 1
        assert isinstance(caught.value.__cause__, RetryExhaustedError)
        assert classify_structured_failure(caught.value) is FailureKind.TRANSIENT

    def test_the_translated_errors_read_as_the_classifier_expects(self) -> None:
        gw, _ = _gateway(EndpointConnectionError(endpoint_url="https://bedrock-runtime"))
        with pytest.raises(RuntimeError) as caught:
            _call(gw)
        assert isinstance(caught.value.__cause__, RetryExhaustedError)
        last = caught.value.__cause__.last_exc
        assert isinstance(last, ConnectionError)
        assert is_transient(last, retry_status=LLM_REQUEST.retry_status)
        assert is_pre_dispatch(last)

    def test_on_its_own_a_transient_failure_is_retried_once(self) -> None:
        gw, client = _gateway(
            _client_error("ThrottlingException", 429), _response({"proposals": []})
        )
        assert _call(gw, retry_policy=None).data == {"proposals": []}
        assert len(client.requests) == 2


class TestNonTransientErrors:
    @pytest.mark.parametrize(
        ("code", "status"),
        [
            ("AccessDeniedException", 403),
            ("ValidationException", 400),
            ("ResourceNotFoundException", 404),
        ],
    )
    def test_the_provider_cannot_serve_it_and_another_may(self, code: str, status: int) -> None:
        gw, client = _gateway(_client_error(code, status))
        with pytest.raises(RuntimeError, match="could not serve") as caught:
            _call(gw, retry_policy=None)
        # Not retried on the same provider, even under the default policy.
        assert len(client.requests) == 1
        cause = caught.value.__cause__
        assert isinstance(cause, BedrockCallError)
        assert (cause.error_code, cause.status_code) == (code, status)
        assert not is_transient(cause, retry_status=LLM_REQUEST.retry_status)
        # Not asked again, but the hedged run moves on to its next model.
        assert classify_structured_failure(caught.value) is FailureKind.PERMANENT

    def test_missing_credentials_are_a_provider_failure(self) -> None:
        gw, _ = _gateway(NoCredentialsError())
        with pytest.raises(RuntimeError, match="NoCredentialsError"):
            _call(gw)

    def test_service_messages_stay_out_of_errors_and_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        gw, _ = _gateway(_client_error("ValidationException", 400))
        with pytest.raises(RuntimeError, match="could not serve") as caught:
            _call(gw, user_prompt="USER-marker")
        assert "SECRET-marker" not in str(caught.value)
        assert "SECRET-marker" not in str(caught.value.__cause__)
        assert "SECRET-marker" not in caplog.text
        assert "USER-marker" not in caplog.text
        assert "ValidationException" in caplog.text


class TestTimeouts:
    def test_each_bound_gets_its_own_client_rounded_up_to_half_a_second(self) -> None:
        built: list[float] = []
        client = _StubClient(_response({"proposals": []}))

        def factory(timeout: float) -> _StubClient:
            built.append(timeout)
            return client

        gw = BedrockStructuredLLMGateway(client_factory=factory)
        _call(gw, timeout_seconds=15.0)
        _call(gw, timeout_seconds=15.0)
        _call(gw, timeout_seconds=9.2)
        _call(gw, timeout_seconds=9.4)
        _call(gw)
        assert built == [15.0, 9.5, 180.0]

    def test_a_real_client_carries_the_bound_and_no_retries_of_its_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
        gw = BedrockStructuredLLMGateway(region="us-east-1")
        client = gw._client(7.2)
        config = client.meta.config
        assert config.read_timeout == 7.5
        assert config.connect_timeout == 5.0
        assert config.retries["total_max_attempts"] == 1
        assert client.meta.region_name == "us-east-1"


class TestWebIdentity:
    _ROLE = "arn:aws:iam::123456789012:role/structured-llm"

    def test_unset_role_leaves_the_standard_chain(self) -> None:
        session = build_botocore_session(region="us-east-1", role_arn=None, audience="aud")
        methods = [p.METHOD for p in session.get_component("credential_provider").providers]
        assert "google-web-identity" not in methods

    def test_role_is_assumed_with_a_google_token_on_first_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        role = self._ROLE
        stubs: list[Stubber] = []
        original = botocore.session.Session.create_client

        def create_client(self: Any, service_name: str, *args: Any, **kwargs: Any) -> Any:
            client = original(self, service_name, *args, **kwargs)
            if service_name == "sts":
                stub = Stubber(client)
                stub.add_response(
                    "assume_role_with_web_identity",
                    {
                        "Credentials": {
                            "AccessKeyId": "ASIAEXAMPLEEXAMPLE",
                            "SecretAccessKey": "secret-secret-secret",
                            "SessionToken": "session-token",
                            "Expiration": datetime.now(UTC) + timedelta(hours=1),
                        }
                    },
                    {
                        "RoleArn": role,
                        "RoleSessionName": "pablo-structured-llm",
                        "WebIdentityToken": "token-for:sts.amazonaws.com",
                    },
                )
                stub.activate()
                stubs.append(stub)
            return client

        monkeypatch.setattr(botocore.session.Session, "create_client", create_client)
        audiences: list[str] = []

        def token_loader(audience: str) -> str:
            audiences.append(audience)
            return f"token-for:{audience}"

        session = build_botocore_session(
            region="us-east-1",
            role_arn=self._ROLE,
            audience="sts.amazonaws.com",
            token_loader=token_loader,
        )
        credentials = session.get_credentials()
        # Deferred: nothing is fetched until a request needs signing.
        assert audiences == []
        frozen = credentials.get_frozen_credentials()
        assert frozen.access_key == "ASIAEXAMPLEEXAMPLE"
        assert frozen.token == "session-token"
        assert audiences == ["sts.amazonaws.com"]
        assert len(stubs) == 1
        stubs[0].assert_no_pending_responses()


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()
    yield exporter
    exporter.clear()


class TestTelemetry:
    def test_usage_tokens_are_recorded_without_content(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        gw, _ = _gateway(_response({"proposals": []}))
        _call(gw, system_prompt="SYSTEM-marker", user_prompt="USER-marker")
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes or {})
        assert attrs[SpanAttributes.LLM_MODEL_NAME] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        assert attrs[SpanAttributes.LLM_SYSTEM] == "bedrock"
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == 120
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] == 9
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] == 129
        haystack = repr(attrs)
        assert "SYSTEM-marker" not in haystack
        assert "USER-marker" not in haystack


class TestResolution:
    def test_bedrock_prefix_routes_to_bedrock(self) -> None:
        assert isinstance(resolve_structured_llm_gateway(HAIKU), BedrockStructuredLLMGateway)

    def test_the_same_gateway_serves_every_bedrock_model(self) -> None:
        assert resolve_structured_llm_gateway(
            "bedrock:us.amazon.nova-pro-v1:0"
        ) is resolve_structured_llm_gateway(HAIKU)

    def test_bare_ids_still_route_to_gemini(self) -> None:
        assert isinstance(
            resolve_structured_llm_gateway("gemini-3.5-flash"), GeminiStructuredLLMGateway
        )


# -- as the fallback route of the interactive policy ---------------------------

#: What the availability-parse end-to-end spec allows one parse.
_INTERACTIVE_BUDGET_SECONDS = 8.0

_FRIDAYS = {
    "proposals": [
        {
            "rule_type": "block_day_of_week",
            "enforcement": "hard",
            "day_of_week": 4,
            "human_summary": "No Fridays.",
            "confidence": 0.95,
        }
    ]
}


def _gemini_504() -> RuntimeError:
    """What the Gemini gateway raises when its single attempt gets a 504."""
    error = RuntimeError("Structured LLM call failed: 504")
    error.__cause__ = RetryExhaustedError(attempts=1, last_exc=httpx.ReadTimeout("504"))
    return error


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    pool = ThreadPoolExecutor(max_workers=4)
    yield pool
    pool.shutdown(wait=False, cancel_futures=True)


class TestAsTheInteractiveFallback:
    def test_a_gemini_error_is_answered_by_bedrock_within_budget(
        self, executor: ThreadPoolExecutor
    ) -> None:
        gemini = FakeStructuredLLMGateway(responses=[_gemini_504()])
        built: list[float] = []
        bedrock_client = _StubClient(_response(_FRIDAYS))

        def factory(timeout: float) -> _StubClient:
            built.append(timeout)
            return bedrock_client

        bedrock = BedrockStructuredLLMGateway(client_factory=factory)
        gateways = {"gemini-3.5-flash": gemini, HAIKU: bedrock}
        hedged = HedgedStructuredLLMGateway(
            fallbacks=[HAIKU], resolve=gateways.__getitem__, executor=executor
        )
        service = AvailabilityRuleParseService(llm_gateway=hedged, model="gemini-3.5-flash")

        started = time.monotonic()
        result = service.parse("No appointments on Fridays")
        elapsed = time.monotonic() - started

        assert [p.rule_type for p in result.proposals] == ["block_day_of_week"]
        assert elapsed < _INTERACTIVE_BUDGET_SECONDS
        # Gemini tried once, then Bedrock at once with the parser's own bound.
        assert len(gemini.calls) == 1
        assert len(bedrock_client.requests) == 1
        assert bedrock_client.requests[0]["modelId"] == HAIKU.removeprefix("bedrock:")
        assert built == [15.0]

    def test_a_bedrock_that_cannot_serve_does_not_end_the_run(
        self, executor: ThreadPoolExecutor
    ) -> None:
        """No model access on the fallback: the run goes back to Gemini."""
        gemini = FakeStructuredLLMGateway(
            responses=[_gemini_504(), StructuredCompletion(data=_FRIDAYS)]
        )
        bedrock, bedrock_client = _gateway(_client_error("AccessDeniedException", 403))
        gateways = {"gemini-3.5-flash": gemini, HAIKU: bedrock}
        hedged = HedgedStructuredLLMGateway(
            fallbacks=[HAIKU], resolve=gateways.__getitem__, executor=executor
        )
        service = AvailabilityRuleParseService(llm_gateway=hedged, model="gemini-3.5-flash")

        result = service.parse("No appointments on Fridays")

        assert [p.rule_type for p in result.proposals] == ["block_day_of_week"]
        assert len(gemini.calls) == 2
        assert len(bedrock_client.requests) == 1

    def test_the_fallback_setting_puts_bedrock_in_the_policy(self) -> None:
        hedged = HedgedStructuredLLMGateway(fallbacks=[HAIKU])
        legs = [leg.model for leg in hedged.policy_for("gemini-3.5-flash", 15.0).legs]
        assert legs == ["gemini-3.5-flash", HAIKU, "gemini-3.5-flash"]


class TestSimulatedHandover:
    """The interactive policy on the routing simulator's virtual clock, with
    the fallback slot filled by a second provider that answers in ``s``.

    What the Bedrock model must achieve for the 8 s budget to hold: a
    primary that fails hands over at once, and one that stalls hands over
    at the 4 s threshold, so the fallback has the rest.
    """

    @pytest.mark.parametrize("seconds", [1.6, 3.0, 3.9])
    def test_a_primary_error_is_answered_by_the_fallback(self, seconds: float) -> None:
        record = simulate_script(
            interactive_policy((SECONDARY,)),
            Script({PRIMARY: (error_504(0.3),), SECONDARY: (ok(seconds),)}),
        )
        assert record.result == "ok"
        assert record.wall == pytest.approx(0.3 + seconds)
        assert record.wall < _INTERACTIVE_BUDGET_SECONDS

    @pytest.mark.parametrize("seconds", [1.6, 3.0, 3.9])
    def test_a_stalled_primary_is_answered_by_the_fallback(self, seconds: float) -> None:
        record = simulate_script(
            interactive_policy((SECONDARY,)),
            Script({PRIMARY: (stall(),), SECONDARY: (ok(seconds),)}),
        )
        assert record.result == "ok"
        assert record.wall == pytest.approx(INTERACTIVE_STALL_AFTER + seconds)
        assert record.wall < _INTERACTIVE_BUDGET_SECONDS


class TestParseEvalRunner:
    def test_the_corpus_can_be_graded_on_bedrock_without_a_vertex_project(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        # The runner sets this if absent; setting it here keeps it from leaking.
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
        bedrock, client = _gateway(_response(_FRIDAYS))
        asked: list[str] = []

        def resolve(model: str) -> BedrockStructuredLLMGateway:
            asked.append(model)
            return bedrock

        monkeypatch.setattr(structured, "resolve_structured_llm_gateway", resolve)
        case = all_cases()[0]

        code = parse_eval.main(["--model", HAIKU, "--case", case.name, "--json"])

        # Graded, pass or fail, rather than refused as a setup error.
        assert code in (0, 1)
        assert asked
        assert set(asked) == {HAIKU}
        assert client.requests[0]["modelId"] == HAIKU.removeprefix("bedrock:")
        assert '"summary"' in capsys.readouterr().out

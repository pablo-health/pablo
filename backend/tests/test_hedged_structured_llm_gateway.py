# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The threaded hedge driver and the gateway built on it.

The policy itself is tested event by event in ``test_reliability_hedge``;
these check that real threads carry it out, and that with nothing
configured the gateway behaves as the single-model retry it replaces.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.reliability import SINGLE_ATTEMPT, RetryExhaustedError
from app.reliability.hedge import INTERACTIVE_STALL_AFTER, FailureKind, HedgePolicy, HedgeRun, Leg
from app.reliability.hedge_sync import run_hedged_sync
from app.services import structured_llm_gateway
from app.services.availability_parse_service import AvailabilityRuleParseService
from app.services.hedged_structured_llm_gateway import (
    HedgedStructuredLLMGateway,
    classify_structured_failure,
)
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
    StructuredOutputTruncatedError,
    register_structured_llm_provider,
    resolve_structured_llm_gateway,
)
from app.settings import Settings
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    pool = ThreadPoolExecutor(max_workers=4)
    yield pool
    pool.shutdown(wait=False, cancel_futures=True)


def _transient(message: str = "504") -> RuntimeError:
    """What a gateway raises when its single attempt hits a transient error."""
    timeout = httpx.ReadTimeout(message)
    exhausted = RetryExhaustedError(attempts=1, last_exc=timeout)
    # As ``raise RetryExhaustedError(...) from exc`` chains it.
    exhausted.__cause__ = timeout
    error = RuntimeError(f"Structured LLM call failed: {message}")
    error.__cause__ = exhausted
    return error


def _always_transient(_exc: BaseException) -> FailureKind:
    return FailureKind.TRANSIENT


class TestRunHedgedSync:
    def test_returns_the_first_legs_answer(self, executor: ThreadPoolExecutor) -> None:
        run: HedgeRun[str] = HedgeRun(HedgePolicy((Leg("a", 1.0),), budget=1.0))
        result = run_hedged_sync(
            run, lambda leg: f"from {leg.model}", classify=_always_transient, executor=executor
        )
        assert result == "from a"

    def test_a_stalled_leg_is_hedged_and_abandoned(self, executor: ThreadPoolExecutor) -> None:
        release = threading.Event()

        def call(leg: Leg) -> str:
            if leg.model == "a":
                release.wait(5)
                return "late"
            return "from b"

        policy = HedgePolicy((Leg("a", 5.0), Leg("b", 5.0)), budget=5.0, hedge_after=0.05)
        started = time.monotonic()
        try:
            result = run_hedged_sync(
                HedgeRun(policy), call, classify=_always_transient, executor=executor
            )
        finally:
            release.set()
        assert result == "from b"
        assert time.monotonic() - started < 1.0

    def test_a_leg_past_its_timeout_is_replaced(self, executor: ThreadPoolExecutor) -> None:
        release = threading.Event()
        calls: list[str] = []

        def call(leg: Leg) -> str:
            calls.append(leg.model)
            if len(calls) == 1:
                release.wait(5)
            return leg.model

        policy = HedgePolicy((Leg("a", 0.05), Leg("b", 0.05)), budget=5.0)
        try:
            result = run_hedged_sync(
                HedgeRun(policy), call, classify=_always_transient, executor=executor
            )
        finally:
            release.set()
        assert result == "b"

    def test_a_failure_moves_on_and_the_last_one_is_raised(
        self, executor: ThreadPoolExecutor
    ) -> None:
        def call(leg: Leg) -> str:
            raise RuntimeError(leg.model)

        policy = HedgePolicy((Leg("a", 1.0), Leg("b", 1.0)), budget=1.0)
        with pytest.raises(RuntimeError, match=r"^b$"):
            run_hedged_sync(HedgeRun(policy), call, classify=_always_transient, executor=executor)

    def test_each_leg_runs_in_the_callers_context(self, executor: ThreadPoolExecutor) -> None:
        marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
        marker.set("caller")
        run: HedgeRun[str] = HedgeRun(HedgePolicy((Leg("a", 1.0),), budget=1.0))
        result = run_hedged_sync(
            run, lambda _leg: marker.get(), classify=_always_transient, executor=executor
        )
        assert result == "caller"


class TestClassify:
    @pytest.mark.parametrize(
        ("error", "kind"),
        [
            (StructuredOutputTruncatedError("cut off"), FailureKind.FATAL),
            (ValueError("LLM returned invalid JSON"), FailureKind.INVALID),
            (_transient(), FailureKind.TRANSIENT),
            (RuntimeError("Structured LLM call failed: 403"), FailureKind.PERMANENT),
            (TimeoutError("timed out"), FailureKind.TRANSIENT),
        ],
    )
    def test_sorts_failures_as_the_retry_engine_would(
        self, error: BaseException, kind: FailureKind
    ) -> None:
        assert classify_structured_failure(error) is kind


_OK = StructuredCompletion(data={"ok": True})


def _complete(gateway: HedgedStructuredLLMGateway, **extra: Any) -> StructuredCompletion:
    return gateway.complete_structured(
        model="flash",
        system_prompt="s",
        user_prompt="u",
        response_schema={"type": "object"},
        max_output_tokens=64,
        **extra,
    )


class TestSingleModel:
    """With nothing configured: one attempt and one retry, as before."""

    def _gateway(
        self, fake: FakeStructuredLLMGateway, executor: ThreadPoolExecutor
    ) -> HedgedStructuredLLMGateway:
        return HedgedStructuredLLMGateway(resolve=lambda _model: fake, executor=executor)

    def test_each_leg_is_a_single_attempt_with_the_callers_timeout(
        self, executor: ThreadPoolExecutor
    ) -> None:
        fake = FakeStructuredLLMGateway(default_response=_OK)
        assert _complete(self._gateway(fake, executor), timeout_seconds=15.0) == _OK
        assert len(fake.calls) == 1
        assert fake.calls[0]["timeout_seconds"] == 15.0
        assert fake.calls[0]["retry_policy"] is SINGLE_ATTEMPT
        assert fake.calls[0]["model"] == "flash"

    def test_a_transient_failure_is_retried_once(self, executor: ThreadPoolExecutor) -> None:
        fake = FakeStructuredLLMGateway(responses=[_transient(), _OK])
        assert _complete(self._gateway(fake, executor)) == _OK
        assert [c["model"] for c in fake.calls] == ["flash", "flash"]

    def test_two_transient_failures_fail(self, executor: ThreadPoolExecutor) -> None:
        fake = FakeStructuredLLMGateway(responses=[_transient(), _transient("again")])
        with pytest.raises(RuntimeError, match="again"):
            _complete(self._gateway(fake, executor))

    @pytest.mark.parametrize(
        "error",
        [
            StructuredOutputTruncatedError("cut off"),
            ValueError("LLM returned invalid JSON"),
            RuntimeError("Structured LLM call failed: 403"),
        ],
    )
    def test_a_non_transient_failure_is_raised_unretried(
        self, error: Exception, executor: ThreadPoolExecutor
    ) -> None:
        fake = FakeStructuredLLMGateway(responses=[error, _OK])
        with pytest.raises(type(error)):
            _complete(self._gateway(fake, executor))
        assert len(fake.calls) == 1

    def test_two_timeouts_fail_as_a_failed_call(self, executor: ThreadPoolExecutor) -> None:
        release = threading.Event()

        class Stalling(FakeStructuredLLMGateway):
            def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
                answer = super().complete_structured(**kwargs)
                release.wait(5)
                return answer

        fake = Stalling(default_response=_OK)
        try:
            with pytest.raises(RuntimeError, match="Structured LLM call failed"):
                _complete(self._gateway(fake, executor), timeout_seconds=0.05)
        finally:
            release.set()
        assert len(fake.calls) == 2


class TestFallbacks:
    def test_each_leg_goes_to_its_own_models_gateway(self, executor: ThreadPoolExecutor) -> None:
        gateways = {
            "flash": FakeStructuredLLMGateway(responses=[ValueError("not JSON")]),
            "anthropic:haiku": FakeStructuredLLMGateway(default_response=_OK),
        }
        hedged = HedgedStructuredLLMGateway(
            fallbacks=["anthropic:haiku"], resolve=gateways.__getitem__, executor=executor
        )
        assert _complete(hedged) == _OK
        assert [c["model"] for c in gateways["anthropic:haiku"].calls] == ["anthropic:haiku"]

    def test_policy_carries_the_configured_fallbacks_and_hedge(self) -> None:
        hedged = HedgedStructuredLLMGateway(fallbacks=["b"], stall_after=6.0)
        policy = hedged.policy_for("a", 15.0)
        assert [leg.model for leg in policy.legs] == ["a", "b", "a"]
        assert policy.hedge_after == 6.0
        assert policy.budget == 25.0

    def test_a_refusing_primary_hands_over_and_is_not_asked_again(
        self, executor: ThreadPoolExecutor
    ) -> None:
        gateways = {
            "flash": FakeStructuredLLMGateway(
                responses=[RuntimeError("Structured LLM call failed: 403")]
            ),
            "other:model": FakeStructuredLLMGateway(responses=[_transient()]),
        }
        hedged = HedgedStructuredLLMGateway(
            fallbacks=["other:model"], resolve=gateways.__getitem__, executor=executor
        )
        with pytest.raises(RuntimeError, match="504"):
            _complete(hedged)
        assert len(gateways["flash"].calls) == 1
        assert len(gateways["other:model"].calls) == 1

    def test_a_registered_provider_serves_its_prefix(self) -> None:
        fake = FakeStructuredLLMGateway(default_response=_OK)
        register_structured_llm_provider("other", lambda: fake)
        try:
            assert resolve_structured_llm_gateway("other:model") is fake
            assert resolve_structured_llm_gateway("flash") is not fake
        finally:
            structured_llm_gateway._registered_providers.pop("other")

    def test_a_prefix_must_be_a_prefix(self) -> None:
        with pytest.raises(ValueError, match="not a provider prefix"):
            register_structured_llm_provider("a:b", FakeStructuredLLMGateway)


class TestInteractiveHandover:
    """A failing or stalling primary is handed over, not waited out."""

    def test_a_failing_primary_is_answered_by_the_fallback_at_once(
        self, executor: ThreadPoolExecutor
    ) -> None:
        gateways = {
            "flash": FakeStructuredLLMGateway(responses=[_transient()]),
            "other:model": FakeStructuredLLMGateway(default_response=_OK),
        }
        hedged = HedgedStructuredLLMGateway(
            fallbacks=["other:model"], resolve=gateways.__getitem__, executor=executor
        )
        started = time.monotonic()
        assert _complete(hedged, timeout_seconds=15.0) == _OK
        assert time.monotonic() - started < 1.0

    def test_a_stalled_primary_gets_its_retry_at_the_threshold(
        self, executor: ThreadPoolExecutor
    ) -> None:
        release = threading.Event()
        calls: list[float] = []

        class StallsFirst(FakeStructuredLLMGateway):
            def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
                calls.append(time.monotonic())
                if len(calls) == 1:
                    release.wait(5)
                return _OK

        hedged = HedgedStructuredLLMGateway(
            stall_after=0.2, resolve=lambda _model: StallsFirst(), executor=executor
        )
        started = time.monotonic()
        try:
            assert _complete(hedged, timeout_seconds=15.0) == _OK
        finally:
            release.set()
        assert len(calls) == 2
        assert 0.15 <= calls[1] - started < 1.0

    def test_the_route_and_failures_are_logged_without_prompt_text(
        self, executor: ThreadPoolExecutor, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = FakeStructuredLLMGateway(responses=[_transient(), _OK])
        hedged = HedgedStructuredLLMGateway(resolve=lambda _model: fake, executor=executor)
        with caplog.at_level(logging.INFO, logger="app.services.hedged_structured_llm_gateway"):
            hedged.complete_structured(
                model="flash",
                system_prompt="SYSTEM-SECRET",
                user_prompt="Jane Doe on Tuesdays",
                response_schema={"type": "object"},
                max_output_tokens=64,
            )
        (line,) = [r.getMessage() for r in caplog.records]
        assert "route=retry" in line
        assert "attempts=2" in line
        assert "failures=flash:ReadTimeout" in line
        assert "latency_ms=" in line
        assert "Jane" not in line
        assert "SYSTEM-SECRET" not in line

    def test_when_every_leg_fails_the_failure_is_logged_and_raised(
        self, executor: ThreadPoolExecutor, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = FakeStructuredLLMGateway(responses=[_transient(), _transient("again")])
        hedged = HedgedStructuredLLMGateway(resolve=lambda _model: fake, executor=executor)
        with (
            caplog.at_level(logging.WARNING, logger="app.services.hedged_structured_llm_gateway"),
            pytest.raises(RuntimeError, match="again"),
        ):
            _complete(hedged)
        (line,) = [r.getMessage() for r in caplog.records]
        assert line.startswith("Structured call failed: attempts=2")
        assert "flash:ReadTimeout,flash:ReadTimeout" in line


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Settings:
    configured = Settings(
        database_url="postgresql://x:x@localhost:5432/x", environment="development", **overrides
    )
    monkeypatch.setattr(
        "app.services.hedged_structured_llm_gateway.get_settings", lambda: configured
    )
    return configured


class TestPerFeatureStallThreshold:
    """A slow but healthy primary is not hedged at its median."""

    def test_nothing_configured_keeps_the_default_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch)
        policy = HedgedStructuredLLMGateway.from_settings().policy_for("flash", 15.0)
        assert policy.hedge_after == INTERACTIVE_STALL_AFTER

    def test_the_global_threshold_applies_to_a_feature_not_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(
            monkeypatch, ai_hedge_after_seconds=3.0, ai_hedge_delays_ms={"other_feature": 9000}
        )
        policy = HedgedStructuredLLMGateway.from_settings().policy_for("flash", 15.0)
        assert policy.hedge_after == 3.0

    def test_a_features_own_threshold_wins_and_is_read_in_milliseconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(
            monkeypatch,
            ai_hedge_after_seconds=3.0,
            ai_hedge_delays_ms={"availability_parse": 7000},
        )
        policy = HedgedStructuredLLMGateway.from_settings().policy_for("flash", 15.0)
        assert policy.hedge_after == 7.0
        assert policy.budget == 25.0

    def test_it_is_read_from_the_environment_as_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_HEDGE_DELAYS_MS", '{"availability_parse": 7000}')
        configured = Settings(
            database_url="postgresql://x:x@localhost:5432/x", environment="development"
        )
        assert configured.hedge_after_for("availability_parse") == 7.0
        assert configured.hedge_after_for("chat") is None

    @pytest.mark.parametrize("delay", [0, -1])
    def test_a_threshold_must_be_positive(self, delay: int) -> None:
        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql://x:x@localhost:5432/x",
                environment="development",
                ai_hedge_delays_ms={"availability_parse": delay},
            )

    def test_a_primary_answering_inside_its_threshold_is_the_only_call(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Slower than the global threshold, inside its own: no second call."""
        gateways = {
            "flash": _Slow(0.3),
            "other:model": FakeStructuredLLMGateway(default_response=_OK),
        }
        _configure(
            monkeypatch,
            ai_hedge_after_seconds=0.05,
            ai_hedge_delays_ms={"availability_parse": 2000},
            ai_fallbacks={"availability_parse": "other:model"},
        )
        hedged = HedgedStructuredLLMGateway.from_settings(resolve=gateways.__getitem__)
        with caplog.at_level(logging.INFO, logger="app.services.hedged_structured_llm_gateway"):
            assert _complete(hedged, timeout_seconds=15.0) == _OK
        (line,) = [r.getMessage() for r in caplog.records]
        assert "route=primary" in line
        assert "attempts=1" in line
        assert "failures=-" in line
        assert gateways["other:model"].calls == []

    def test_the_same_primary_under_the_global_threshold_starts_a_second_call(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The control: without its own threshold the fallback joins and is thrown away."""
        release = threading.Event()
        gateways = {
            "flash": _Slow(0.3),
            "other:model": _Stalls(release),
        }
        _configure(
            monkeypatch,
            ai_hedge_after_seconds=0.05,
            ai_fallbacks={"availability_parse": "other:model"},
        )
        hedged = HedgedStructuredLLMGateway.from_settings(resolve=gateways.__getitem__)
        try:
            with caplog.at_level(logging.INFO, logger="app.services.hedged_structured_llm_gateway"):
                assert _complete(hedged, timeout_seconds=15.0) == _OK
        finally:
            release.set()
        (line,) = [r.getMessage() for r in caplog.records]
        assert "attempts=2" in line
        assert "failures=other:model:abandoned" in line

    def test_a_primary_stalled_past_its_threshold_hands_over(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        release = threading.Event()
        fallback_started: list[float] = []

        class Fallback(FakeStructuredLLMGateway):
            def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
                fallback_started.append(time.monotonic())
                return super().complete_structured(**kwargs)

        gateways: dict[str, FakeStructuredLLMGateway] = {
            "flash": _Stalls(release),
            "other:model": Fallback(default_response=_OK),
        }
        _configure(
            monkeypatch,
            ai_hedge_delays_ms={"availability_parse": 300},
            ai_fallbacks={"availability_parse": "other:model"},
        )
        hedged = HedgedStructuredLLMGateway.from_settings(resolve=gateways.__getitem__)
        started = time.monotonic()
        try:
            with caplog.at_level(logging.INFO, logger="app.services.hedged_structured_llm_gateway"):
                assert _complete(hedged, timeout_seconds=15.0) == _OK
        finally:
            release.set()
        assert 0.25 <= fallback_started[0] - started < 1.5
        (line,) = [r.getMessage() for r in caplog.records]
        assert "route=fallback" in line
        assert "attempts=2" in line
        assert "failures=flash:abandoned" in line


class _Slow(FakeStructuredLLMGateway):
    """Answers, after ``seconds``."""

    def __init__(self, seconds: float) -> None:
        super().__init__(default_response=_OK)
        self._seconds = seconds

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        answer = super().complete_structured(**kwargs)
        time.sleep(self._seconds)
        return answer


class _Stalls(FakeStructuredLLMGateway):
    """Answers only once ``release`` is set, or after 5 s."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__(default_response=_OK)
        self._release = release

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        answer = super().complete_structured(**kwargs)
        self._release.wait(5)
        return answer


class TestAvailabilityParser:
    def test_the_parser_goes_through_the_hedged_gateway_by_default(self) -> None:
        gateway = AvailabilityRuleParseService()._llm_gateway
        assert isinstance(gateway, HedgedStructuredLLMGateway)
        assert [leg.model for leg in gateway.policy_for("flash", 15.0).legs] == ["flash", "flash"]

    def test_a_failed_parse_is_retried_through_the_policy_within_the_budget(
        self, executor: ThreadPoolExecutor
    ) -> None:
        fake = FakeStructuredLLMGateway(responses=[_transient("503"), _NO_FRIDAYS])
        hedged = HedgedStructuredLLMGateway(resolve=lambda _model: fake, executor=executor)
        service = AvailabilityRuleParseService(llm_gateway=hedged)

        started = time.monotonic()
        result = service.parse("No appointments on Fridays")

        assert time.monotonic() - started < 1.0
        assert [p.rule_type for p in result.proposals] == ["block_day_of_week"]
        assert [c["timeout_seconds"] for c in fake.calls] == [15.0, 15.0]

    def test_a_stalled_parse_holds_the_budget_at_the_default_threshold(
        self, executor: ThreadPoolExecutor
    ) -> None:
        """Real time, real threshold: the retry starts at 4 s, not at 15."""
        release = threading.Event()
        calls: list[float] = []

        class StallsFirst(FakeStructuredLLMGateway):
            def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
                calls.append(time.monotonic())
                if len(calls) == 1:
                    release.wait(10)
                return _NO_FRIDAYS

        hedged = HedgedStructuredLLMGateway(resolve=lambda _model: StallsFirst(), executor=executor)
        service = AvailabilityRuleParseService(llm_gateway=hedged)

        started = time.monotonic()
        try:
            result = service.parse("No appointments on Fridays")
        finally:
            release.set()
        elapsed = time.monotonic() - started

        assert [p.rule_type for p in result.proposals] == ["block_day_of_week"]
        assert INTERACTIVE_STALL_AFTER <= elapsed < 8.0
        assert len(calls) == 2


_NO_FRIDAYS = StructuredCompletion(
    data={
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
)

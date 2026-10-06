# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A structured gateway that serves each call through a hedge policy.

An interactive caller asks for one model and gets an answer from whichever
leg of :meth:`~app.reliability.hedge.HedgePolicy.interactive` produces a
usable one first: the requested model, then each configured fallback, then
the requested model once more. Each leg goes to the gateway for its own
model's provider with a single attempt, so the provider's own retry never
runs: a failure hands over to the next leg at once, and a leg still running
at the stall threshold (4 s unless configured) gets the next leg beside it.
The same model is asked at most twice, and not again once it has refused
the request outright.

A fallback is named by model string alone. ``resolve`` (by default
:func:`resolve_structured_llm_gateway`) maps it to its provider's gateway,
so a new provider joins by registering a gateway for its prefix, with no
change to the policy.

Every call ends within 25 s, the deadline of the ``LLM_REQUEST`` retry
this replaces: a leg that starts late is given what is left of it.

A long call (drafting, importing or deriving a note) uses the same legs
with no stall threshold, built by :func:`generation_gateway` from the
feature's entry in ``ai_fallbacks``. Attempts run one at a time, in this
order:

1. the requested model, for up to 180 s;
2. each fallback once, in the configured order, each started only when the
   leg before it failed transiently, gave an unusable answer, refused the
   request, or ran out of time;
3. the requested model once more.

The whole sequence ends within 300 s, so a primary that stalls to its
timeout still leaves the fallback two minutes. A truncated answer ends the
sequence at once and reaches the caller, whose own retry at a larger output
budget runs the sequence again; nothing else retries inside it.

Each call logs one line: the route that answered (primary, retry or
fallback), the legs started, the latency and the class of each failure.
Model names and error classes only, never prompt or answer text.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from ..reliability import (
    LLM_REQUEST,
    SINGLE_ATTEMPT,
    RetryExhaustedError,
    is_transient,
)
from ..reliability.hedge import (
    INTERACTIVE_STALL_AFTER,
    FailureKind,
    HedgePolicy,
    HedgeRun,
    Leg,
    LegTimeoutError,
)
from ..reliability.hedge_sync import run_hedged_sync
from ..settings import get_settings
from .ai_features import AIFeature
from .structured_llm_gateway import (
    _STRUCTURED_LLM_TIMEOUT_SECONDS,
    StructuredCompletion,
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    get_default_structured_llm_gateway,
    resolve_structured_llm_gateway,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Executor

    from ..reliability import RetryPolicy

logger = logging.getLogger(__name__)

#: Every call, retries included, ends within this long: the deadline of the
#: one-retry ``LLM_REQUEST`` preset these legs replace.
_BUDGET_SECONDS = 25.0

#: A long call's whole sequence of attempts ends within this long. Each
#: attempt keeps the structured client's own 180 s bound, the time a
#: reasoning model may need for a full note, so a fallback after a primary
#: that stalled to its end still has two minutes.
GENERATION_BUDGET_SECONDS = 300.0

#: Workers shared by every hedged call in the process. An abandoned leg
#: holds one until its own timeout, so this is sized for a few requests'
#: worth of stalled legs at once, not for throughput.
_LEG_WORKERS = 16

_executor_holder: list[ThreadPoolExecutor] = []


def _leg_executor() -> ThreadPoolExecutor:
    if not _executor_holder:
        _executor_holder.append(
            ThreadPoolExecutor(max_workers=_LEG_WORKERS, thread_name_prefix="structured-llm-leg")
        )
    return _executor_holder[0]


def classify_structured_failure(exc: BaseException) -> FailureKind:
    """Sort a leg's failure by what it says about the legs after it.

    A transient failure is one the single-attempt retry engine gave up on
    (``RetryExhaustedError``) or a transient error further down the chain.
    A truncated answer is fatal: every leg shares the same output cap.
    Anything else (no access, no such model, a request the provider
    rejects) is that model's answer however often it is asked, but not
    necessarily another's, so it is permanent: the model is not asked
    again and the next one is.
    """
    if isinstance(exc, StructuredOutputTruncatedError):
        return FailureKind.FATAL
    if isinstance(exc, ValueError):
        return FailureKind.INVALID
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, RetryExhaustedError) or is_transient(
            cause, retry_status=LLM_REQUEST.retry_status
        ):
            return FailureKind.TRANSIENT
        cause = cause.__cause__
    return FailureKind.PERMANENT


def _route(run: HedgeRun[Any], winner: int) -> str:
    legs = run.legs
    if winner == 0:
        return "primary"
    return "retry" if legs[winner].model == legs[0].model else "fallback"


def _failures(run: HedgeRun[Any]) -> str:
    return ",".join(f"{model}:{what}" for _index, model, what in run.trail) or "-"


class HedgedStructuredLLMGateway(StructuredLLMGateway):
    """Route one structured call across models by :class:`HedgePolicy`."""

    def __init__(
        self,
        *,
        fallbacks: Sequence[str] = (),
        stall_after: float | None = INTERACTIVE_STALL_AFTER,
        resolve: Callable[[str], StructuredLLMGateway] = resolve_structured_llm_gateway,
        executor: Executor | None = None,
        budget: float = _BUDGET_SECONDS,
    ) -> None:
        self._fallbacks = tuple(fallbacks)
        self._stall_after = stall_after
        self._resolve = resolve
        self._executor = executor
        self._budget = budget

    @classmethod
    def from_settings(
        cls,
        resolve: Callable[[str], StructuredLLMGateway] = resolve_structured_llm_gateway,
        feature: str = AIFeature.AVAILABILITY_PARSE,
    ) -> HedgedStructuredLLMGateway:
        """An interactive gateway with ``feature``'s fallbacks, if it has any."""
        settings = get_settings()
        return cls(
            fallbacks=settings.fallbacks_for(feature),
            stall_after=settings.ai_hedge_after_seconds or INTERACTIVE_STALL_AFTER,
            resolve=resolve,
        )

    def policy_for(self, model: str, timeout_seconds: float | None) -> HedgePolicy:
        return HedgePolicy.interactive(
            model,
            self._fallbacks,
            attempt_timeout=timeout_seconds or _STRUCTURED_LLM_TIMEOUT_SECONDS,
            budget=self._budget,
            stall_after=self._stall_after,
        )

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
        timeout_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> StructuredCompletion:
        # The policy owns attempts; a retry policy has nothing to apply to.
        del retry_policy

        def call(leg: Leg) -> StructuredCompletion:
            return self._resolve(leg.model).complete_structured(
                model=leg.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=response_schema,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                thinking_budget=thinking_budget,
                # The caller's bound, or less when the leg starts late.
                timeout_seconds=leg.timeout,
                retry_policy=SINGLE_ATTEMPT,
            )

        run: HedgeRun[StructuredCompletion] = HedgeRun(self.policy_for(model, timeout_seconds))
        started = time.monotonic()
        try:
            completion = run_hedged_sync(
                run,
                call,
                classify=classify_structured_failure,
                executor=self._executor or _leg_executor(),
            )
        except Exception as exc:
            logger.warning(
                "Structured call failed: attempts=%d latency_ms=%d failures=%s error=%s",
                run.started,
                int((time.monotonic() - started) * 1000),
                _failures(run),
                type(exc).__name__,
            )
            if isinstance(exc, LegTimeoutError):
                # Callers already handle a failed call as a RuntimeError.
                raise RuntimeError(f"Structured LLM call failed: {exc}") from exc
            raise
        winner = run.winner if run.winner is not None else 0
        logger.info(
            "Structured call answered: route=%s model=%s attempts=%d latency_ms=%d failures=%s",
            _route(run, winner),
            run.legs[winner].model,
            run.started,
            int((time.monotonic() - started) * 1000),
            _failures(run),
        )
        return completion


def generation_gateway(
    feature: str, single: StructuredLLMGateway | None = None
) -> StructuredLLMGateway:
    """The gateway a long structured call for ``feature`` goes through.

    With no fallbacks named for ``feature``, that is ``single`` (the default
    Gemini gateway unless given), unchanged: one model, its own retry. With
    fallbacks named, a hedged gateway that runs the attempts listed in this
    module's docstring one at a time, never side by side. ``single``, when
    given, answers every leg whatever its model, as the end-to-end stand-in
    does.
    """
    fallbacks = get_settings().fallbacks_for(feature)
    if not fallbacks:
        return single or get_default_structured_llm_gateway()
    return HedgedStructuredLLMGateway(
        fallbacks=fallbacks,
        stall_after=None,
        resolve=resolve_structured_llm_gateway if single is None else (lambda _model: single),
        budget=GENERATION_BUDGET_SECONDS,
    )


__all__ = [
    "GENERATION_BUDGET_SECONDS",
    "HedgedStructuredLLMGateway",
    "classify_structured_failure",
    "generation_gateway",
]

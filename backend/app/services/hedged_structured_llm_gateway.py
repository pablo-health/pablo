# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A structured gateway that serves each call through a hedge policy.

An interactive caller asks for one model and gets an answer from whichever
leg of :class:`~app.reliability.hedge.HedgePolicy` produces a usable one
first: the requested model, then any configured fallbacks, then each of
them once more. Each leg goes to the gateway for its own model's
provider, with no retry of its own, since a retry here is simply the
next leg.

With no fallbacks and no hedge delay configured, which is the default,
this is one attempt and one retry on one model, bounded the way the
gateway's own ``LLM_REQUEST`` retry bounds it: each attempt by the
caller's timeout, the same failures retried, and no new attempt once
25 s have passed. The one difference is that the retry starts at once,
where ``LLM_REQUEST`` slept up to half a second of jittered backoff (or a
429's ``Retry-After``, capped at 4 s) first.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from ..reliability import (
    LLM_REQUEST,
    SINGLE_ATTEMPT,
    RetryExhaustedError,
    is_transient,
)
from ..reliability.hedge import FailureKind, HedgePolicy, HedgeRun, Leg, LegTimeoutError
from ..reliability.hedge_sync import run_hedged_sync
from ..settings import get_settings
from .structured_llm_gateway import (
    _STRUCTURED_LLM_TIMEOUT_SECONDS,
    StructuredCompletion,
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    resolve_structured_llm_gateway,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Executor

    from ..reliability import RetryPolicy

#: No new leg starts after this long: the deadline of the one-retry
#: ``LLM_REQUEST`` preset these legs replace.
_BUDGET_SECONDS = 25.0

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
    """Sort a leg's failure the way the gateway's own retry would have.

    A transient failure is one the single-attempt retry engine gave up on
    (``RetryExhaustedError``) or a transient error further down the chain.
    A truncated answer is fatal: every leg shares the same output cap.
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
    return FailureKind.FATAL


class HedgedStructuredLLMGateway(StructuredLLMGateway):
    """Route one structured call across models by :class:`HedgePolicy`."""

    def __init__(
        self,
        *,
        fallbacks: Sequence[str] = (),
        hedge_after: float | None = None,
        resolve: Callable[[str], StructuredLLMGateway] = resolve_structured_llm_gateway,
        executor: Executor | None = None,
    ) -> None:
        self._fallbacks = tuple(fallbacks)
        self._hedge_after = hedge_after
        self._resolve = resolve
        self._executor = executor

    @classmethod
    def from_settings(cls) -> HedgedStructuredLLMGateway:
        settings = get_settings()
        return cls(
            fallbacks=settings.flash_fallback_models,
            hedge_after=settings.ai_hedge_after_seconds,
        )

    def policy_for(self, model: str, timeout_seconds: float | None) -> HedgePolicy:
        return HedgePolicy.for_models(
            model,
            self._fallbacks,
            attempt_timeout=timeout_seconds or _STRUCTURED_LLM_TIMEOUT_SECONDS,
            budget=_BUDGET_SECONDS,
            hedge_after=self._hedge_after,
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
                timeout_seconds=timeout_seconds,
                retry_policy=SINGLE_ATTEMPT,
            )

        run: HedgeRun[StructuredCompletion] = HedgeRun(self.policy_for(model, timeout_seconds))
        try:
            return run_hedged_sync(
                run,
                call,
                classify=classify_structured_failure,
                executor=self._executor or _leg_executor(),
            )
        except LegTimeoutError as exc:
            # Callers already handle a failed call as a RuntimeError.
            raise RuntimeError(f"Structured LLM call failed: {exc}") from exc


__all__ = ["HedgedStructuredLLMGateway", "classify_structured_failure"]

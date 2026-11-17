# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Recorded sessions of listed practices, transcribed by the model stand-in.

A practice listed in ``MODEL_STANDIN_PRACTICES`` (:mod:`.model_standin`) has
its recorded sessions answered by the stand-in instead of the transcription
provider. The answer is shaped as the provider's jobs are once the poller has
read them: one job per channel, each already carrying its utterances. So the
poll that follows finds nothing pending, asks the provider nothing, and merges
the transcript exactly as it merges a provider's.

A stand-in job is marked ``standin`` and its id is not the provider's, so
nothing later tries to fetch or delete it there.
"""

from __future__ import annotations

from typing import Any

import httpx

_TIMEOUT_SECONDS = 30.0
_CHANNELS = ("Therapist", "Client")


def is_standin_job(job: dict[str, Any]) -> bool:
    """Whether the job was answered by the stand-in rather than the provider."""
    return bool(job.get("standin"))


def standin_session_jobs(base_url: str, *, offset_seconds: float = 0.0) -> list[dict[str, Any]]:
    """The stand-in's transcript of a session, as one completed job per channel.

    ``POST {base_url}/v1/transcribe-session`` answers ``{"utterances": [...]}``,
    each with a ``speaker`` (a channel), ``start``, ``end`` and ``text``.
    ``offset_seconds`` places a segment on the whole-session timeline, as the
    provider's segment jobs are shifted.

    Raises:
        httpx.HTTPError: the stand-in did not answer.
        ValueError: the stand-in answered with something other than utterances.
    """
    response = httpx.post(
        base_url.rstrip("/") + "/v1/transcribe-session", json={}, timeout=_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    utterances = response.json().get("utterances")
    if not isinstance(utterances, list):
        raise ValueError("session stand-in returned no utterances")
    jobs: list[dict[str, Any]] = []
    for channel in _CHANNELS:
        jobs.append(
            {
                "transcript_id": f"standin-{channel.lower()}",
                "speaker": channel,
                "offset_map": [[0.0, offset_seconds]],
                "diarized": False,
                "standin": True,
                "utterances": [
                    {
                        "start": float(u["start"]) + offset_seconds,
                        "end": float(u["end"]) + offset_seconds,
                        "speaker": channel,
                        "text": str(u["text"]),
                    }
                    for u in utterances
                    if u.get("speaker") == channel
                ],
            }
        )
    return jobs


__all__ = ["is_standin_job", "standin_session_jobs"]

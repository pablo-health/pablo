# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One log shape for a recorded session that will not become a note.

A recording can stop short of a note at several points: its audio never
finishes uploading, the transcription job cannot be submitted, the provider
errors or never finishes, or note generation fails. Each point logs its own
message, so nothing downstream could treat them as the same outcome. Every one
of them now also carries the fields below, so a deployment can alert on
``jsonPayload.event = "session_audio_failure"`` and say which stage failed
without reading a message.

The fields are identifiers and a stage name only: never patient data, and
never the free text of an exception.
"""

from __future__ import annotations

from typing import Literal

SESSION_AUDIO_FAILURE_EVENT = "session_audio_failure"

#: Where the recording stopped: its upload, the transcription submit, the
#: transcription job itself, or note generation.
SessionAudioStage = Literal["upload", "transcription_submit", "transcription", "note"]

#: Upload starts for one session that never finished before it counts as stuck.
#: A start is one attempt by the recorder. A healthy upload needs one, so a
#: third means two attempts already failed to reach storage.
UPLOAD_STARTS_BEFORE_ALERT = 3


def session_audio_failure_extra(stage: SessionAudioStage, user_id: str) -> dict[str, str]:
    """``extra=`` for the ERROR line logged where a recording stops short of a note.

    ``user_id`` is the clinician the session belongs to. It is passed
    explicitly because the transcription and note workers run outside a
    request, where the log context carries no user.
    """
    return {"event": SESSION_AUDIO_FAILURE_EVENT, "stage": stage, "user_id": user_id}

# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice session orchestration — ASR + Gemini text + ElevenLabs TTS pipeline."""

import asyncio
import logging
import re
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypedDict

from ..models.practice import PracticeMode, PracticeTopic
from ..settings import Settings, get_settings
from .asr import AsrProviderFactory
from .asr.base import AsrProvider
from .elevenlabs_tts_service import ElevenLabsTTSService
from .gemini_text_service import GeminiTextSession

logger = logging.getLogger(__name__)


class ConversationEntry(TypedDict):
    role: str
    text: str
    elapsed: float


# Binary frame direction bytes
DIRECTION_CLIENT_TO_SERVER = 0x01
DIRECTION_PATIENT_VOICE = 0x02
DIRECTION_THERAPIST_VOICE = 0x03

HEADER_SIZE = 4


@dataclass
class ActivePracticeSession:
    """In-memory state for an active practice session."""

    session_id: str
    user_id: str
    topic: PracticeTopic
    mode: PracticeMode
    asr: AsrProvider
    gemini: GeminiTextSession
    tts_patient: ElevenLabsTTSService
    started_at: float = field(default_factory=time.monotonic)
    turn_id: int = 0
    sequence_out: int = 0
    is_paused: bool = False
    conversation_history: list[ConversationEntry] = field(default_factory=list)
    _duration_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _demo_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _send_text: Callable[[dict[str, object]], Awaitable[None]] | None = field(
        default=None, repr=False
    )
    _send_binary: Callable[[bytes], Awaitable[None]] | None = field(default=None, repr=False)


def _pack_audio_header(direction: int, is_final: bool, sequence: int) -> bytes:
    flags = 0x01 if is_final else 0x00
    return struct.pack(">BBH", direction, flags, sequence & 0xFFFF)


class PracticeSessionManager:
    """Manages active WebSocket practice sessions."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._active: dict[str, ActivePracticeSession] = {}

    @property
    def active_sessions(self) -> dict[str, ActivePracticeSession]:
        return self._active

    async def start_practice_session(
        self,
        session_id: str,
        user_id: str,
        topic: PracticeTopic,
        mode: PracticeMode,
        send_text: Callable[[dict[str, object]], Awaitable[None]],
        send_binary: Callable[[bytes], Awaitable[None]],
    ) -> ActivePracticeSession:
        """Initialize a practice session with ASR + Gemini + ElevenLabs."""
        tts_patient = ElevenLabsTTSService(self._settings)

        # Placeholder — filled after session_state exists
        session_state = ActivePracticeSession(
            session_id=session_id,
            user_id=user_id,
            topic=topic,
            mode=mode,
            asr=None,  # type: ignore[arg-type]
            gemini=None,  # type: ignore[arg-type]
            tts_patient=tts_patient,
            _send_text=send_text,
            _send_binary=send_binary,
        )
        self._active[session_id] = session_state

        # Create Gemini text session
        gemini = self._create_gemini_session(session_state, topic)
        session_state.gemini = gemini
        await gemini.start()

        # Create ASR provider — transcripts trigger Gemini generation
        asr = self._create_asr(session_state)
        session_state.asr = asr
        await asr.start()

        # Duration watchdog
        max_seconds = self._settings.practice_max_duration_minutes * 60
        session_state._duration_task = asyncio.create_task(
            self._duration_watchdog(session_id, max_seconds)
        )

        if mode == PracticeMode.DEMO:
            session_state._demo_task = asyncio.create_task(
                _DemoOrchestrator(self._settings, self._active, session_state).run()
            )

        return session_state

    def _create_asr(self, session_state: ActivePracticeSession) -> AsrProvider:
        async def on_transcript(text: str, is_final: bool) -> None:
            if not is_final or not text.strip():
                return
            logger.info("Therapist said: %s", text[:100])
            elapsed = time.monotonic() - session_state.started_at
            session_state.conversation_history.append(
                ConversationEntry(role="therapist", text=text, elapsed=elapsed)
            )

            # Notify client that therapist speech was recognized
            if session_state._send_text:
                await session_state._send_text(
                    {
                        "type": "status",
                        "state": "processing",
                        "speaker": "patient",
                        "turn_id": session_state.turn_id,
                    }
                )

            # Generate patient response
            await session_state.gemini.generate_response(text)

        async def on_error(error: str) -> None:
            logger.error("ASR error in session %s: %s", session_state.session_id, error)
            if session_state._send_text:
                await session_state._send_text(
                    {
                        "type": "error",
                        "code": "ASR_ERROR",
                        "message": error,
                        "recoverable": True,
                    }
                )

        return AsrProviderFactory.create(
            settings=self._settings,
            on_transcript=on_transcript,
            on_error=on_error,
        )

    def _create_gemini_session(
        self, session_state: ActivePracticeSession, topic: PracticeTopic
    ) -> GeminiTextSession:
        async def on_text_response(text: str, is_turn_complete: bool) -> None:
            await self._handle_patient_response(session_state, text, is_turn_complete)

        async def on_error(code: str, is_fatal: bool) -> None:
            msg_type = "fatal_error" if is_fatal else "error"
            if session_state._send_text:
                await session_state._send_text(
                    {
                        "type": msg_type,
                        "code": code,
                        "message": f"AI service error: {code}",
                        "recoverable": not is_fatal,
                    }
                )

        return GeminiTextSession(
            settings=self._settings,
            system_prompt=topic.patient_system_prompt,
            on_text_response=on_text_response,
            on_error=on_error,
        )

    @staticmethod
    def _strip_stage_directions(text: str) -> str:
        """Remove stage directions that TTS would speak as literal text.

        Gemini sometimes generates actions like *wrings paws* or (sighs deeply)
        despite prompt instructions. Strip them so TTS only speaks dialogue.
        """
        # Remove *italicized actions* and (parenthesized actions)
        cleaned = re.sub(r"\*[^*]+\*", "", text)
        cleaned = re.sub(r"\([^)]*\)", "", cleaned)
        # Remove [bracketed actions]
        cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    async def _handle_patient_response(
        self,
        session: ActivePracticeSession,
        text: str,
        is_turn_complete: bool,
    ) -> None:
        """Pipe Gemini text through ElevenLabs TTS to client."""
        text = self._strip_stage_directions(text)
        if not text:
            return
        elapsed = time.monotonic() - session.started_at
        session.conversation_history.append(
            ConversationEntry(role="patient", text=text, elapsed=elapsed)
        )

        if session._send_text:
            await session._send_text(
                {
                    "type": "status",
                    "state": "speaking",
                    "speaker": "patient",
                    "turn_id": session.turn_id,
                }
            )

        async def on_audio_chunk(pcm: bytes, is_final: bool) -> None:
            if is_final and not pcm:
                return
            session.sequence_out = (session.sequence_out + 1) % 65536
            header = _pack_audio_header(DIRECTION_PATIENT_VOICE, is_final, session.sequence_out)
            if session._send_binary:
                await session._send_binary(header + pcm)

        await session.tts_patient.synthesize_stream(text, on_audio_chunk)

        if is_turn_complete:
            session.turn_id += 1
            if session._send_text:
                await session._send_text(
                    {
                        "type": "status",
                        "state": "listening",
                        "speaker": "patient",
                        "turn_id": session.turn_id,
                    }
                )

    async def handle_audio(self, session_id: str, pcm_data: bytes) -> None:
        """Forward therapist audio to ASR for transcription."""
        session = self._active.get(session_id)
        if not session or session.is_paused or session.mode == PracticeMode.DEMO:
            return
        await session.asr.send_audio(pcm_data)

    async def end_session(self, session_id: str) -> tuple[int, list[ConversationEntry]]:
        """Clean shutdown. Returns (duration_seconds, conversation_history)."""
        session = self._active.pop(session_id, None)
        if not session:
            return 0, []

        conversation_history = list(session.conversation_history)

        for task in (session._duration_task, session._demo_task):
            if task and not task.done():
                task.cancel()

        await session.asr.stop()
        await session.gemini.close()
        await session.tts_patient.close()
        return int(time.monotonic() - session.started_at), conversation_history

    async def _duration_watchdog(self, session_id: str, max_seconds: int) -> None:
        """Auto-end session after max duration."""
        try:
            await asyncio.sleep(max_seconds)
            session = self._active.get(session_id)
            if session and session._send_text:
                await session._send_text(
                    {
                        "type": "fatal_error",
                        "code": "SESSION_DURATION_EXCEEDED",
                        "message": "Maximum session duration reached.",
                        "session_id": session_id,
                    }
                )
            await self.end_session(session_id)  # conversation history not needed here
        except asyncio.CancelledError:
            pass


class _DemoOrchestrator:
    """Runs a fully automated AI therapist + AI patient conversation."""

    def __init__(
        self,
        settings: Settings,
        active_map: dict[str, ActivePracticeSession],
        session: ActivePracticeSession,
    ) -> None:
        self._settings = settings
        self._active_map = active_map
        self._session = session

    async def run(self) -> None:
        tts_therapist: ElevenLabsTTSService | None = None
        therapist_gemini: GeminiTextSession | None = None

        try:
            tts_therapist = self._create_therapist_tts()
            therapist_gemini = self._create_therapist_gemini()
            await therapist_gemini.start()

            for turn in range(10):
                if not self._is_active():
                    break
                await self._therapist_turn(turn, therapist_gemini, tts_therapist)
                if not self._is_active():
                    break
                await self._patient_turn()

            if self._is_active() and self._session._send_text:
                await self._session._send_text(
                    {
                        "type": "session_ended",
                        "session_id": self._session.session_id,
                        "duration_seconds": int(time.monotonic() - self._session.started_at),
                    }
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Demo error for session %s", self._session.session_id)
        finally:
            if therapist_gemini:
                await therapist_gemini.close()
            if tts_therapist:
                await tts_therapist.close()

    def _is_active(self) -> bool:
        return self._session.session_id in self._active_map

    def _create_therapist_tts(self) -> ElevenLabsTTSService:
        voice = self._settings.elevenlabs_therapist_voice_id or None
        return ElevenLabsTTSService(self._settings, voice_id=voice)

    def _create_therapist_gemini(self) -> GeminiTextSession:
        async def noop_error(_code: str, _fatal: bool) -> None:
            pass

        return GeminiTextSession(
            settings=self._settings,
            system_prompt=self._session.topic.therapist_system_prompt,
            on_text_response=self._on_therapist_response,
            on_error=noop_error,
        )

    async def _on_therapist_response(self, text: str, _is_complete: bool) -> None:
        self._last_therapist_text = text

    async def _therapist_turn(
        self,
        turn: int,
        gemini: GeminiTextSession,
        tts: ElevenLabsTTSService,
    ) -> None:
        self._last_therapist_text = ""

        if turn == 0:
            await gemini.send_text("Begin the therapy session. Greet your patient warmly.")
        else:
            last_patient = next(
                (
                    h["text"]
                    for h in reversed(self._session.conversation_history)
                    if h["role"] == "patient"
                ),
                "",
            )
            if last_patient:
                await gemini.send_text(last_patient)

        await asyncio.sleep(1)
        text = self._last_therapist_text
        if not text or not self._is_active():
            return

        elapsed = time.monotonic() - self._session.started_at
        self._session.conversation_history.append(
            ConversationEntry(role="therapist", text=text, elapsed=elapsed)
        )

        if self._session._send_text:
            await self._session._send_text(
                {
                    "type": "status",
                    "state": "speaking",
                    "speaker": "therapist",
                    "turn_id": self._session.turn_id,
                }
            )

        seq = 0

        async def on_audio(pcm: bytes, is_final: bool) -> None:
            nonlocal seq
            if is_final and not pcm:
                return
            seq = (seq + 1) % 65536
            header = _pack_audio_header(DIRECTION_THERAPIST_VOICE, is_final, seq)
            if self._session._send_binary:
                await self._session._send_binary(header + pcm)

        await tts.synthesize_stream(text, on_audio)

        if self._session._send_text:
            await self._session._send_text(
                {
                    "type": "status",
                    "state": "listening",
                    "speaker": "therapist",
                    "turn_id": self._session.turn_id,
                }
            )
        await asyncio.sleep(2)

    async def _patient_turn(self) -> None:
        last_therapist = next(
            (
                h["text"]
                for h in reversed(self._session.conversation_history)
                if h["role"] == "therapist"
            ),
            "",
        )
        if last_therapist:
            await self._session.gemini.send_text(last_therapist)
        await asyncio.sleep(4)
        self._session.turn_id += 1


# Module-level singleton
_manager: PracticeSessionManager | None = None


def get_session_manager() -> PracticeSessionManager:
    """Get the module-level session manager singleton."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        _manager = PracticeSessionManager()
    return _manager

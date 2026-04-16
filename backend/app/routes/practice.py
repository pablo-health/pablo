# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice Mode API routes — REST endpoints + WebSocket for real-time audio."""

import asyncio
import contextlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from ..auth.service import (
    TenantContext,
    get_tenant_context,
    verify_firebase_token,
)
from ..models import UploadTranscriptToSessionRequest
from ..models.practice import (
    CreatePracticeSessionRequest,
    EndPracticeSessionResponse,
    PracticeMode,
    PracticeSessionDetailResponse,
    PracticeSessionListItem,
    PracticeSessionListResponse,
    PracticeSessionResponse,
    PracticeTopicListResponse,
    PracticeTopicResponse,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..repositories import (
    get_session_repository as _session_repo_factory,
)
from ..services.practice_service import (
    PracticeConcurrentLimitError,
    PracticeDailyLimitError,
    PracticeService,
    PracticeSessionNotEndableError,
    PracticeSessionNotFoundError,
    PracticeTopicNotFoundError,
    format_conversation_as_transcript,
)
from ..services.practice_session_manager import HEADER_SIZE, ConversationEntry, get_session_manager
from ..services.session_service import SessionService
from ..services.soap_generation_service import MeetingTranscriptionSOAPService
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/practice", tags=["practice"])


# --- WebSocket ticket store ---
# Short-lived, single-use opaque tickets so the Firebase JWT never appears
# in WebSocket URLs (which get logged by proxies, CDNs, and browsers).

_WS_TICKET_TTL = timedelta(seconds=30)


@dataclass
class _WsTicket:
    user_id: str
    decoded_token: dict[str, object]
    expires_at: datetime


@dataclass
class _WsTicketStore:
    """In-memory single-use WebSocket ticket store.

    Safe for single-worker deployments (Cloud Run with --workers 1).
    Move to Redis if scaling to multiple workers.
    """

    _tickets: dict[str, _WsTicket] = field(default_factory=dict)

    def create(
        self,
        user_id: str,
        decoded_token: dict[str, object],
    ) -> str:
        self._purge_expired()
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = _WsTicket(
            user_id=user_id,
            decoded_token=decoded_token,
            expires_at=datetime.now(UTC) + _WS_TICKET_TTL,
        )
        return ticket

    def exchange(self, ticket: str) -> _WsTicket | None:
        """Consume a ticket (single-use). Returns None if invalid/expired."""
        self._purge_expired()
        entry = self._tickets.pop(ticket, None)
        if entry and entry.expires_at > datetime.now(UTC):
            return entry
        return None

    def _purge_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [k for k, v in self._tickets.items() if v.expires_at <= now]
        for k in expired:
            del self._tickets[k]


_ws_ticket_store = _WsTicketStore()


# --- Dependencies ---


def _get_practice_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PracticeService:
    return PracticeService(
        session_repo=_session_repo_factory(),
        patient_repo=_patient_repo_factory(),
        settings=get_settings(),
    )


def _parse_session_metadata(notes: str | None) -> tuple[str, PracticeMode]:
    """Extract topic_id and mode from the session notes field."""
    topic_id = ""
    mode = PracticeMode.PRACTICE
    if notes:
        for part in notes.split(";"):
            if part.startswith("topic_id="):
                topic_id = part.split("=", 1)[1]
            elif part.startswith("mode="):
                mode = PracticeMode(part.split("=", 1)[1])
    return topic_id, mode


# --- REST: Topics ---


@router.get("/topics")
def list_topics(
    _ctx: TenantContext = Depends(get_tenant_context),
    service: PracticeService = Depends(_get_practice_service),
) -> PracticeTopicListResponse:
    """List all available practice topics."""
    topics = service.get_topics()
    data = [
        PracticeTopicResponse(
            id=t.id,
            name=t.name,
            description=t.description,
            category=t.category,
            estimated_duration_minutes=t.estimated_duration_minutes,
        )
        for t in topics
    ]
    return PracticeTopicListResponse(data=data, total=len(data))


@router.get("/topics/{topic_id}")
def get_topic(
    topic_id: str,
    _ctx: TenantContext = Depends(get_tenant_context),
    service: PracticeService = Depends(_get_practice_service),
) -> PracticeTopicResponse:
    """Get a single practice topic by ID."""
    topic = service.get_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    return PracticeTopicResponse(
        id=topic.id,
        name=topic.name,
        description=topic.description,
        category=topic.category,
        estimated_duration_minutes=topic.estimated_duration_minutes,
    )


# --- REST: Sessions ---


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_practice_session(
    request: CreatePracticeSessionRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    service: PracticeService = Depends(_get_practice_service),
) -> PracticeSessionResponse:
    """Create a practice session. Call before opening the WebSocket."""
    settings = get_settings()
    try:
        session, topic = service.create_session(ctx.user_id, request)
    except PracticeTopicNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found"
        ) from None
    except PracticeDailyLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
            headers={"Retry-After": "86400"},
        ) from None
    except PracticeConcurrentLimitError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You already have an active practice session.",
        ) from None

    scheme = "ws" if settings.is_development else "wss"
    host = "localhost:8000" if settings.is_development else "api.pablo.health"

    ticket = _ws_ticket_store.create(
        ctx.user_id,
        {"uid": ctx.user_id},
    )

    return PracticeSessionResponse(
        session_id=session.id,
        topic_id=topic.id,
        topic_name=topic.name,
        mode=request.mode,
        status=session.status,
        ws_url=f"{scheme}://{host}/api/practice/ws",
        ws_ticket=ticket,
        created_at=session.created_at,
    )


class _WsTicketResponse(BaseModel):
    ticket: str
    expires_in_seconds: int = 30


@router.post("/ws-ticket")
def create_ws_ticket(
    ctx: TenantContext = Depends(get_tenant_context),
) -> _WsTicketResponse:
    """Exchange a Firebase JWT (in Authorization header) for a short-lived WebSocket ticket.

    The ticket is single-use and expires in 30 seconds. Pass it as
    ``?ticket=<ticket>`` when connecting to the WebSocket endpoint.
    This avoids putting the long-lived JWT in the URL where it would
    be logged by proxies, CDNs, and browser history.
    """
    ticket = _ws_ticket_store.create(
        ctx.user_id,
        {"uid": ctx.user_id},
    )
    return _WsTicketResponse(ticket=ticket)


@router.get("/sessions")
def list_practice_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    ctx: TenantContext = Depends(get_tenant_context),
    service: PracticeService = Depends(_get_practice_service),
) -> PracticeSessionListResponse:
    """List the current user's practice sessions."""
    sessions, total = service.list_sessions(ctx.user_id, page, page_size)
    data = []
    for s in sessions:
        topic_id, mode = _parse_session_metadata(s.notes)
        topic = service.get_topic(topic_id)
        data.append(
            PracticeSessionListItem(
                session_id=s.id,
                topic_id=topic_id,
                topic_name=topic.name if topic else "Unknown",
                mode=mode,
                status=s.status,
                duration_seconds=(s.duration_minutes or 0) * 60 if s.duration_minutes else None,
                started_at=s.started_at,
                ended_at=s.ended_at,
                created_at=s.created_at,
                has_soap_note=s.soap_note is not None,
            )
        )
    return PracticeSessionListResponse(data=data, total=total, page=page, page_size=page_size)


@router.get("/sessions/{session_id}")
def get_practice_session(
    session_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    service: PracticeService = Depends(_get_practice_service),
) -> PracticeSessionDetailResponse:
    """Get full detail for a practice session."""
    session = service.get_session(session_id, ctx.user_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    topic_id, mode = _parse_session_metadata(session.notes)
    topic = service.get_topic(topic_id)

    return PracticeSessionDetailResponse(
        session_id=session.id,
        topic_id=topic_id,
        topic_name=topic.name if topic else "Unknown",
        mode=mode,
        status=session.status,
        duration_seconds=(session.duration_minutes or 0) * 60 if session.duration_minutes else None,
        started_at=session.started_at,
        ended_at=session.ended_at,
        created_at=session.created_at,
        soap_note=session.soap_note.to_narrative_model() if session.soap_note else None,
    )


@router.post("/sessions/{session_id}/end")
def end_practice_session(
    session_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    service: PracticeService = Depends(_get_practice_service),
) -> EndPracticeSessionResponse:
    """End a practice session via REST (fallback when WebSocket is closed)."""
    try:
        session = service.end_session(session_id, ctx.user_id)
    except PracticeSessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from None
    except PracticeSessionNotEndableError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot end session in status '{e.current_status}'",
        ) from None

    duration = (session.duration_minutes or 0) * 60 if session.duration_minutes else None
    return EndPracticeSessionResponse(
        session_id=session.id, status=session.status, duration_seconds=duration
    )


# --- WebSocket ---


@router.websocket("/ws")
async def practice_websocket(
    websocket: WebSocket,
    ticket: str = Query(""),
    token: str = Query(""),
) -> None:
    """Practice Mode WebSocket endpoint.

    Auth via ``?ticket=`` (preferred) or ``?token=`` (deprecated).
    Tickets are short-lived, single-use opaque strings obtained from
    ``POST /ws-ticket`` or the session-creation response. This avoids
    putting long-lived JWTs in URLs.

    Always accepts the connection first so the client receives a proper
    WebSocket close frame (not a raw TCP reset) on auth/config errors.
    """
    # Accept first — closing before accept produces a TCP reset behind
    # reverse proxies (Cloud Run), which clients see as "Socket not connected".
    await websocket.accept()

    settings = get_settings()
    if not settings.practice_enabled:
        await websocket.send_json(
            {
                "type": "fatal_error",
                "code": "PRACTICE_DISABLED",
                "message": "Practice mode is not available",
            }
        )
        await websocket.close(code=4009, reason="Practice mode disabled")
        return

    # Authenticate — prefer ticket, fall back to token (deprecated)
    user_id: str | None = None
    decoded_token: dict[str, object] = {}

    if ticket:
        ws_ticket = _ws_ticket_store.exchange(ticket)
        if ws_ticket:
            user_id = ws_ticket.user_id
            decoded_token = ws_ticket.decoded_token
    elif token:
        logger.warning("WebSocket connected with ?token= (deprecated) — migrate to ?ticket=")
        try:
            decoded_token = verify_firebase_token(token)
            # Enforce MFA on deprecated token path (ticket path already enforces via require_mfa)
            if settings.require_mfa and not settings.is_development and settings.auth_mode != "iap":
                firebase_claims: dict[str, object] = decoded_token.get("firebase", {})  # type: ignore[assignment]
                if not firebase_claims.get("sign_in_second_factor"):
                    await websocket.close(code=4003, reason="MFA required")
                    return
            user_id = decoded_token.get("uid")  # type: ignore[assignment]
        except HTTPException:
            pass

    if not user_id:
        await websocket.send_json(
            {"type": "fatal_error", "code": "AUTH_FAILED", "message": "Invalid or expired ticket"}
        )
        await websocket.close(code=4001, reason="Auth failed")
        return

    await websocket.send_json({"type": "auth_result", "status": "ok", "user_id": user_id})

    try:
        session_repo = _session_repo_factory()
        patient_repo = _patient_repo_factory()
        practice_svc = PracticeService(
            session_repo=session_repo,
            patient_repo=patient_repo,
            settings=settings,
        )
        soap_service = MeetingTranscriptionSOAPService()
        session_svc = SessionService(session_repo, patient_repo, soap_service)
        handler = _WebSocketHandler(websocket, user_id, practice_svc, session_svc)
        await handler.run()
    except Exception:
        logger.exception("WebSocket setup failed after auth")


class _WebSocketHandler:
    """Encapsulates WebSocket message loop to keep the route function small."""

    def __init__(
        self,
        ws: WebSocket,
        user_id: str,
        service: PracticeService,
        session_service: SessionService,
    ) -> None:
        self._ws = ws
        self._user_id = user_id
        self._service = service
        self._session_service = session_service
        self._manager = get_session_manager()
        self._active_session_id: str | None = None

    async def _send_text(self, msg: dict[str, object]) -> None:
        with contextlib.suppress(Exception):
            await self._ws.send_json(msg)

    async def _send_binary(self, data: bytes) -> None:
        with contextlib.suppress(Exception):
            await self._ws.send_bytes(data)

    async def run(self) -> None:
        """Main message loop."""
        idle_deadline = time.monotonic() + 10

        try:
            while True:
                timeout = self._compute_timeout(idle_deadline)
                if timeout is not None and timeout <= 0:
                    await self._ws.close(code=4008, reason="Idle timeout")
                    return

                try:
                    message = await asyncio.wait_for(self._ws.receive(), timeout=timeout or 30)
                except TimeoutError:
                    await self._cleanup_and_close(4008, "Idle timeout")
                    return

                if "text" in message:
                    should_close = await self._handle_control_message(message["text"])
                    if should_close:
                        return
                elif message.get("bytes"):
                    await self._handle_binary(message["bytes"])

        except WebSocketDisconnect:
            await self._cleanup()
        except Exception:
            logger.exception("WebSocket error")
            await self._cleanup()

    def _compute_timeout(self, idle_deadline: float) -> float | None:
        if not self._active_session_id:
            return max(0, idle_deadline - time.monotonic())
        return 30

    async def _handle_control_message(self, raw: str) -> bool:
        """Handle a JSON control message. Returns True if connection should close."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_text(
                {
                    "type": "error",
                    "code": "INVALID_MESSAGE",
                    "message": "Malformed JSON",
                    "recoverable": True,
                }
            )
            return False

        msg_type = msg.get("type", "")

        if msg_type == "session_start":
            return await self._handle_session_start(msg)
        if msg_type == "session_end":
            return await self._handle_session_end()
        if msg_type == "audio_pause":
            session = self._manager.active_sessions.get(self._active_session_id or "")
            if session:
                session.is_paused = True
        elif msg_type == "audio_resume":
            session = self._manager.active_sessions.get(self._active_session_id or "")
            if session:
                session.is_paused = False
        elif msg_type == "ping":
            await self._send_text(
                {"type": "pong", "ts": msg.get("ts", 0), "server_ts": int(time.time() * 1000)}
            )
        return False

    async def _handle_session_start(self, msg: dict) -> bool:  # type: ignore[type-arg]
        session_id = msg.get("session_id", "")
        session = self._service.get_session(session_id, self._user_id)
        if not session:
            await self._ws.close(code=4002, reason="Session not found")
            return True

        topic_id, mode = _parse_session_metadata(session.notes)
        topic = self._service.get_topic(topic_id)
        if not topic:
            await self._ws.close(code=4002, reason="Topic not found")
            return True

        self._service.start_session(session_id, self._user_id)
        # Set active session ID before starting the practice session so the
        # idle timeout is disabled while ASR/Gemini/TTS initialize (~5-10s).
        self._active_session_id = session_id
        try:
            await self._manager.start_practice_session(
                session_id=session_id,
                user_id=self._user_id,
                topic=topic,
                mode=mode,
                send_text=self._send_text,
                send_binary=self._send_binary,
            )
        except Exception:
            self._active_session_id = None
            with contextlib.suppress(Exception):
                self._service.end_session(session_id, self._user_id)
            raise

        await self._send_text(
            {
                "type": "session_started",
                "session_id": session_id,
                "topic_id": topic_id,
                "topic_name": topic.name,
                "mode": mode.value,
                "audio_config": {
                    "input_sample_rate": 16000,
                    "output_sample_rate": 24000,
                    "encoding": "pcm_s16le",
                    "channels": 1,
                },
            }
        )
        return False

    async def _handle_session_end(self) -> bool:
        if self._active_session_id:
            duration, conversation_history = await self._manager.end_session(
                self._active_session_id
            )
            with contextlib.suppress(Exception):
                self._service.end_session(self._active_session_id, self._user_id)

            await self._generate_soap(self._active_session_id, conversation_history)

            await self._send_text(
                {
                    "type": "session_ended",
                    "session_id": self._active_session_id,
                    "duration_seconds": duration,
                }
            )
            self._active_session_id = None
        await self._ws.close(code=1000)
        return True

    async def _handle_binary(self, raw: bytes) -> None:
        if len(raw) > HEADER_SIZE and self._active_session_id:
            pcm_data = raw[HEADER_SIZE:]
            await self._manager.handle_audio(self._active_session_id, pcm_data)

    async def _cleanup_and_close(self, code: int, reason: str) -> None:
        await self._cleanup()
        with contextlib.suppress(Exception):
            await self._ws.close(code=code, reason=reason)

    async def _cleanup(self) -> None:
        if self._active_session_id:
            _, conversation_history = await self._manager.end_session(self._active_session_id)
            with contextlib.suppress(Exception):
                self._service.end_session(self._active_session_id, self._user_id)
            await self._generate_soap(self._active_session_id, conversation_history)

    async def _generate_soap(
        self, session_id: str, conversation_history: list[ConversationEntry]
    ) -> None:
        """Format conversation as transcript and trigger SOAP pipeline."""
        if not conversation_history:
            return
        try:
            transcript_content = format_conversation_as_transcript(conversation_history)
            request = UploadTranscriptToSessionRequest(format="txt", content=transcript_content)
            await asyncio.to_thread(
                self._session_service.upload_transcript_to_session,
                session_id,
                self._user_id,
                request,
            )
            logger.info("SOAP note generated for practice session %s", session_id)
        except Exception:
            logger.exception("SOAP generation failed for practice session %s", session_id)

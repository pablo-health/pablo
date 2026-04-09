# Practice Mode API — Contract Specification

**Version:** 1.0
**Date:** March 2026
**Status:** Draft for Review

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [REST Endpoints](#rest-endpoints)
   - [Topics](#topics)
   - [Practice Sessions](#practice-sessions)
4. [WebSocket Protocol](#websocket-protocol)
   - [Connection Lifecycle](#connection-lifecycle)
   - [Control Messages](#control-messages)
   - [Audio Frames](#audio-frames)
   - [Heartbeat](#heartbeat)
   - [Reconnection](#reconnection)
5. [Data Models](#data-models)
6. [Session Integration](#session-integration)
7. [Rate Limiting](#rate-limiting)
8. [Error Reference](#error-reference)

---

## Overview

Practice Mode enables therapists to conduct simulated therapy sessions with Pablo Bear, an AI patient powered by Gemini Live API. The therapist speaks naturally via microphone; audio streams to the backend over WebSocket; the backend relays to Gemini Live, which returns Pablo's voice response.

The companion app plays Pablo's audio through system speakers. AudioCaptureKit captures both channels (mic = therapist, system = Pablo), and the existing transcription → SOAP note pipeline processes the result identically to a real session.

**Key Design Principles:**
- Practice sessions are NOT PHI — Pablo Bear is a fictional character with whimsical scenarios
- Same session lifecycle as real sessions (`TherapySession` with `source="practice"`)
- WebSocket for real-time audio; REST for CRUD and topic catalog
- Client is a thin audio pipe — all conversation intelligence is server-side
- Cross-platform: same API for macOS and Windows clients

**Architecture:**

```
Client (macOS/Windows)          Backend (FastAPI, GCP)          Gemini Live API
┌──────────────────┐           ┌─────────────────────┐         ┌──────────────┐
│ Mic → PCM 16kHz  │──── WS ──▶│ Practice Service    │── WS ──▶│ audio in     │
│                  │           │                     │         │              │
│ Speaker ← PCM   │◀─── WS ───│ Audio relay         │◀── WS ──│ audio out    │
│ (system audio)   │           │                     │         │ (24kHz)      │
│                  │           │ Session state       │         │ built-in VAD │
│ AudioCaptureKit  │           │ Topic catalog       │         │ built-in TTS │
│ (records both)   │           │ Rate limiting       │         └──────────────┘
└──────────────────┘           └─────────────────────┘
```

---

## Authentication

### REST Endpoints

Same as all other API endpoints: Firebase ID token in `Authorization: Bearer <token>` header. Uses existing `get_current_user()` dependency. BAA acceptance is NOT required (practice data is not PHI).

### WebSocket Endpoint

Firebase ID token in query parameter:

```
wss://api.pablo.health/api/practice/ws?token=<firebase_id_token>
```

**Why query parameter:** The browser/native WebSocket API does not support custom headers on the upgrade request. The `Authorization: Bearer` pattern does not work for WebSocket connections.

The token is validated **before** the WebSocket connection is accepted. Failed auth returns a close frame with code `4001` — the connection never upgrades.

**Security notes:**
- Firebase ID tokens are short-lived (1 hour)
- The token appears in server access logs, which is acceptable for short-lived tokens on a controlled GCP backend
- The client must refresh and reconnect if the token expires during a long session

---

## REST Endpoints

### Topics

#### `GET /api/practice/topics`

List all available practice topics.

**Auth:** `get_current_user()` (logged in, no BAA required)

**Response:** `200 OK`

```json
{
  "data": [
    {
      "id": "generalized_anxiety",
      "name": "Generalized Anxiety",
      "description": "Pablo is worried about honey supply chain disruptions and can't sleep.",
      "category": "anxiety",
      "estimated_duration_minutes": 10
    },
    {
      "id": "work_stress",
      "name": "Work Stress",
      "description": "New job as park ranger, imposter syndrome, demanding boss.",
      "category": "work",
      "estimated_duration_minutes": 10
    }
  ],
  "total": 6
}
```

#### `GET /api/practice/topics/{topic_id}`

Get a single topic by ID.

**Auth:** `get_current_user()`

**Response:** `200 OK`

```json
{
  "id": "generalized_anxiety",
  "name": "Generalized Anxiety",
  "description": "Pablo is worried about honey supply chain disruptions and can't sleep.",
  "category": "anxiety",
  "estimated_duration_minutes": 10
}
```

**Errors:**
- `404` — Topic not found

---

### Practice Sessions

#### `POST /api/practice/sessions`

Create a practice session. Call this BEFORE opening the WebSocket — the returned `session_id` is used in the WebSocket `session_start` message.

**Auth:** `get_current_user()`

**Request:**

```json
{
  "topic_id": "generalized_anxiety"
}
```

**Response:** `201 Created`

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "topic_id": "generalized_anxiety",
  "topic_name": "Generalized Anxiety",
  "status": "scheduled",
  "ws_url": "wss://api.pablo.health/api/practice/ws",
  "created_at": "2026-03-30T14:30:00Z"
}
```

**Side effects:**
- Creates a `TherapySession` with `source="practice"`, `status="scheduled"`
- Auto-creates a "Pablo Bear" patient for this user if one doesn't exist (idempotent)

**Errors:**
- `404` — Topic not found
- `429` — Daily session limit exceeded or concurrent session active

---

#### `GET /api/practice/sessions`

List the current user's practice sessions, most recent first.

**Auth:** `get_current_user()`

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int | 1 | Page number (1-indexed) |
| `page_size` | int | 20 | Items per page (1-50) |

**Response:** `200 OK`

```json
{
  "data": [
    {
      "session_id": "550e8400-e29b-41d4-a716-446655440000",
      "topic_id": "generalized_anxiety",
      "topic_name": "Generalized Anxiety",
      "status": "pending_review",
      "duration_seconds": 342,
      "started_at": "2026-03-30T14:30:05Z",
      "ended_at": "2026-03-30T14:35:47Z",
      "created_at": "2026-03-30T14:30:00Z",
      "has_soap_note": true
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 20
}
```

---

#### `GET /api/practice/sessions/{session_id}`

Get full details for a practice session, including the SOAP note if generated.

**Auth:** `get_current_user()`

**Response:** `200 OK`

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "topic_id": "generalized_anxiety",
  "topic_name": "Generalized Anxiety",
  "status": "pending_review",
  "duration_seconds": 342,
  "started_at": "2026-03-30T14:30:05Z",
  "ended_at": "2026-03-30T14:35:47Z",
  "created_at": "2026-03-30T14:30:00Z",
  "soap_note": {
    "subjective": "Client (Pablo Bear) reports persistent worry about honey supply...",
    "objective": "Client appeared anxious, fidgeting, spoke rapidly...",
    "assessment": "Generalized anxiety with sleep disturbance...",
    "plan": "1. Discuss sleep hygiene strategies..."
  }
}
```

**Errors:**
- `404` — Session not found or belongs to another user

---

#### `POST /api/practice/sessions/{session_id}/end`

End a practice session via REST. Use this as a fallback when the WebSocket is already closed (app crash, network loss). If the WebSocket is still open, send `session_end` over the WebSocket instead.

**Auth:** `get_current_user()`

**Response:** `200 OK`

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "recording_complete",
  "duration_seconds": 342
}
```

**Errors:**
- `404` — Session not found
- `409` — Session is not in a state that can be ended (already ended, finalized, etc.)

---

## WebSocket Protocol

### Connection Lifecycle

**Endpoint:** `wss://api.pablo.health/api/practice/ws?token=<firebase_jwt>`

The WebSocket carries two types of frames:
- **Text frames** — JSON control messages (session lifecycle, status, errors, heartbeat)
- **Binary frames** — PCM audio data with a 4-byte header

```
Client                          Backend                         Gemini Live
  │                                │                                │
  │─── wss connect (token) ───────▶│                                │
  │                                │  [verify Firebase JWT]         │
  │◀── auth_result {ok} ──────────│                                │
  │                                │                                │
  │─── session_start ─────────────▶│                                │
  │    {session_id, topic_id}      │─── open Gemini WS ───────────▶│
  │                                │    (system prompt, voice cfg)  │
  │◀── session_started ───────────│◀── setup complete ─────────────│
  │    {session_id, audio_config}  │                                │
  │                                │                                │
  │══ binary: therapist audio ═══▶│══ relay PCM ══════════════════▶│
  │◀═ binary: Pablo audio ═══════│◀═ relay PCM ══════════════════│
  │◀── status {speaking} ────────│                                │
  │◀── status {listening} ───────│                                │
  │    ... (conversation continues)                                │
  │                                │                                │
  │─── session_end ───────────────▶│─── close Gemini WS ──────────▶│
  │◀── session_ended ─────────────│                                │
  │    {session_id, duration}      │                                │
  │                                │                                │
  │─── [WS close] ───────────────▶│                                │
```

**Timing constraints:**
- Client must send `session_start` within 10 seconds of `auth_result`, or the server closes with code `4008`
- The server sends `session_started` only after the Gemini Live connection is established (typically < 2 seconds)

---

### Control Messages

All control messages are JSON objects sent as WebSocket **text frames**. Every message has a `type` field.

#### Client → Server

##### `session_start`

Begin a new practice session. The `session_id` must reference a session created via `POST /api/practice/sessions`.

```json
{
  "type": "session_start",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

The `topic_id` is not sent here — it was already set when the session was created via REST.

##### `session_resume`

Resume an active session after a disconnection.

```json
{
  "type": "session_resume",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "last_sequence": 1234
}
```

`last_sequence` is the highest sequence number the client received before disconnection. The server uses this for diagnostics only — audio is not replayed.

##### `session_end`

End the current session.

```json
{
  "type": "session_end"
}
```

##### `audio_pause`

Temporarily mute — stop sending audio to Gemini. The client may still send binary frames, but the server drops them.

```json
{
  "type": "audio_pause"
}
```

##### `audio_resume`

Resume audio streaming after a pause.

```json
{
  "type": "audio_resume"
}
```

##### `ping`

Client-initiated heartbeat.

```json
{
  "type": "ping",
  "ts": 1711756800000
}
```

`ts` is the client's Unix timestamp in milliseconds. Used for round-trip latency measurement.

---

#### Server → Client

##### `auth_result`

Sent immediately after connection acceptance.

```json
{
  "type": "auth_result",
  "status": "ok",
  "user_id": "firebase_uid_123"
}
```

##### `session_started`

Session is active. Gemini Live connection is established. Client should begin sending audio.

```json
{
  "type": "session_started",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "topic_id": "generalized_anxiety",
  "topic_name": "Generalized Anxiety",
  "audio_config": {
    "input_sample_rate": 16000,
    "output_sample_rate": 24000,
    "encoding": "pcm_s16le",
    "channels": 1
  }
}
```

The `audio_config` confirms the expected audio format. The client should validate that its capture/playback matches.

##### `session_ended`

Session has ended. The Gemini connection is closed. The session status is now `recording_complete`.

```json
{
  "type": "session_ended",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "duration_seconds": 342
}
```

After receiving this, the client should close the WebSocket and proceed with the normal post-session flow (upload transcript, trigger SOAP pipeline).

##### `status`

Pablo's conversational state changed. The client uses this to drive UI updates (waveform animation, status indicator).

```json
{
  "type": "status",
  "state": "listening",
  "turn_id": 7
}
```

| State | Meaning | Client UI |
|-------|---------|-----------|
| `listening` | Pablo is waiting for the therapist to speak | Waveform idle, mic indicator active |
| `processing` | Gemini is generating a response | Brief "thinking" indicator |
| `speaking` | Pablo is speaking (audio frames incoming) | Waveform animating, Pablo Bear glow |

`turn_id` increments with each Pablo response. Useful for diagnostics.

##### `pong`

Heartbeat response.

```json
{
  "type": "pong",
  "ts": 1711756800000,
  "server_ts": 1711756800005
}
```

`ts` echoes the client's timestamp. `server_ts` is the server's Unix timestamp in milliseconds. The client can compute round-trip latency as `now - ts` and clock skew as `server_ts - ts`.

##### `error`

Non-fatal error. The session can continue.

```json
{
  "type": "error",
  "code": "GEMINI_TIMEOUT",
  "message": "AI response timed out. Please try speaking again.",
  "recoverable": true
}
```

##### `fatal_error`

Fatal error. The session is over. The client should close the WebSocket and show an error message.

```json
{
  "type": "fatal_error",
  "code": "GEMINI_CONNECTION_LOST",
  "message": "Lost connection to AI service. Session ended.",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### Audio Frames

Audio is sent as WebSocket **binary frames** with a 4-byte header followed by raw PCM data.

#### Client → Server (Therapist Audio)

```
Byte    Field          Type              Description
───────────────────────────────────────────────────────
[0]     direction      uint8             0x01 (client-to-server)
[1]     reserved       uint8             0x00
[2-3]   sequence       uint16 BE         Wraps at 65535
[4..]   pcm_data       bytes             PCM 16-bit signed LE, 16kHz, mono
```

**Chunk size:** 20ms of audio at 16kHz = 640 bytes of PCM. With the 4-byte header, each frame is **644 bytes**.

20ms is the standard VoIP frame size — small enough for low latency, large enough to avoid frame overhead domination. The client should send one frame every 20ms while the session is active.

**Sample rate:** The client captures mic audio at the system sample rate (typically 48kHz on macOS) and downsamples to 16kHz before sending. 16kHz is what Gemini Live API expects as input.

#### Server → Client (Pablo Audio)

```
Byte    Field          Type              Description
───────────────────────────────────────────────────────
[0]     direction      uint8             0x02 (server-to-client)
[1]     flags          uint8             Bit 0: is_final (last chunk for this turn)
                                         Bits 1-7: reserved
[2-3]   sequence       uint16 BE         Wraps at 65535
[4..]   pcm_data       bytes             PCM 16-bit signed LE, 24kHz, mono
```

**Sample rate:** 24kHz — Gemini Live API's native output rate. Passed through unmodified for lowest latency. The client's `AVAudioPlayer` (macOS) or WASAPI (Windows) handles 24kHz natively.

**The `is_final` flag** (bit 0 of the flags byte) indicates the last audio chunk for the current Pablo turn. The client uses this to:
1. Flush the audio playback buffer
2. Stop the waveform animation
3. Know that Pablo has stopped speaking

When `is_final` is set, the server also sends a `status` control message with `state: "listening"`.

**Sequence numbers** are for client-side diagnostics (detecting drops, measuring jitter). The server does not rely on them — WebSocket guarantees ordered delivery within a connection.

---

### Heartbeat

The client sends a `ping` control message every **15 seconds**. The server responds with `pong`.

If the server receives no message (audio or control) for **30 seconds**, it considers the connection dead and cleans up. If the client receives no `pong` for **30 seconds**, it should attempt reconnection.

This is the application-level heartbeat, separate from the WebSocket protocol-level ping/pong (which FastAPI/Starlette handles automatically for connection liveness).

---

### Reconnection

If the client detects a disconnection (WebSocket close, network error, heartbeat timeout):

1. Reconnect to `wss://api.pablo.health/api/practice/ws?token=<fresh_token>`
2. After `auth_result`, send `session_resume` (not `session_start`)
3. If the session is still active on the server, the server re-establishes the Gemini connection, replays conversation context, and sends `session_started`
4. If the session has expired (cleaned up after 30 seconds of disconnection), the server sends `error` with code `SESSION_EXPIRED`

**Reconnection window:** The server keeps session state alive for **30 seconds** after a client disconnects without sending `session_end`. After 30 seconds, the Gemini connection is closed and the session transitions to `recording_complete`.

**State preserved across reconnection:**
- Session ID and topic
- Conversation history (replayed to Gemini as text context)
- Turn count and timing

**State NOT preserved:**
- In-flight audio frames (audio is real-time, not buffered for replay)
- The exact Gemini Live connection (a new one is opened with context replay)

---

## Data Models

### Practice Topic

Topics are static data loaded from `practice_topics.json` at server startup.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | URL-safe identifier (e.g., `"generalized_anxiety"`) |
| `name` | string | Display name (e.g., `"Generalized Anxiety"`) |
| `description` | string | One-line summary of Pablo's presenting issue |
| `category` | string | Grouping: `"anxiety"`, `"mood"`, `"relationship"`, `"work"`, `"life_transition"` |
| `estimated_duration_minutes` | int | Suggested session length |

### Topic Catalog (v1)

| ID | Name | Pablo's Presenting Issue |
|----|------|--------------------------|
| `generalized_anxiety` | Generalized Anxiety | Worried about honey supply chain disruptions, can't sleep |
| `work_stress` | Work Stress | New job as park ranger, imposter syndrome, demanding boss |
| `grief_and_loss` | Grief & Loss | Best friend (a rabbit) moved to another forest |
| `relationship_issues` | Relationship Issues | Partner wants to hibernate longer, communication breakdown |
| `depression` | Depression | Lost interest in fishing, favorite activity |
| `life_transition` | Life Transition | Cubs leaving the den, empty nest feelings |

### Pydantic Models (Backend)

```python
# models/practice.py

class CreatePracticeSessionRequest(BaseModel):
    topic_id: str = Field(..., min_length=1, max_length=100)

class PracticeSessionResponse(BaseModel):
    session_id: str
    topic_id: str
    topic_name: str
    status: str
    ws_url: str
    created_at: str

class PracticeSessionDetailResponse(BaseModel):
    session_id: str
    topic_id: str
    topic_name: str
    status: str
    duration_seconds: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str
    soap_note: SOAPNoteModel | None = None

class PracticeSessionListItem(BaseModel):
    session_id: str
    topic_id: str
    topic_name: str
    status: str
    duration_seconds: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str
    has_soap_note: bool = False

class PracticeSessionListResponse(BaseModel):
    data: list[PracticeSessionListItem]
    total: int
    page: int
    page_size: int

class PracticeTopicResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str
    estimated_duration_minutes: int

class PracticeTopicListResponse(BaseModel):
    data: list[PracticeTopicResponse]
    total: int
```

---

## Session Integration

Practice sessions are real `TherapySession` objects — they flow through the same pipeline as clinical sessions.

### Enum Addition

```python
# models/enums.py
class SessionSource(StrEnum):
    WEB = "web"
    COMPANION = "companion"
    CALENDAR = "calendar"
    PRACTICE = "practice"       # NEW
```

### The Pablo Bear Patient

Each user gets one synthetic patient record, auto-created on first practice session:

| Field | Value |
|-------|-------|
| `id` | `practice-{user_id}` (deterministic, idempotent) |
| `user_id` | The therapist's user ID |
| `first_name` | `Pablo` |
| `last_name` | `Bear` |
| `status` | `active` |
| `date_of_birth` | `null` |
| `diagnosis` | `null` |

The Pablo Bear patient is filtered out of clinical views by checking `source="practice"` on associated sessions.

### Status Lifecycle

Practice sessions follow the same status transitions as companion sessions:

```
POST /api/practice/sessions              →  scheduled
WebSocket session_start                  →  in_progress
WebSocket session_end (or REST /end)     →  recording_complete
Audio upload + transcript upload         →  transcribing → queued → processing
SOAP note generated                      →  pending_review
Therapist reviews                        →  finalized
```

No changes to `VALID_TRANSITIONS` in `session_service.py` — the existing state machine works as-is.

### Data Retention

- Practice sessions are tagged with `source="practice"` — immutable, set at creation
- **Auto-delete after 30 days** (configurable via `PRACTICE_SESSION_TTL_DAYS`)
- Excluded from clinical analytics and reporting queries (filter `source != "practice"`)
- Excluded from patient record exports and legal holds
- Practice recordings use separate storage (or no encryption — they contain no PHI)
- SOAP notes from practice sessions display a banner: "PRACTICE SESSION — Not a clinical record"

### Post-Session Pipeline

After the WebSocket session ends, the client runs the normal post-session flow:

1. AudioCaptureKit stops recording (two files: therapist mic, system audio)
2. Client uploads audio via `POST /api/sessions/{session_id}/upload-audio` (existing endpoint)
3. Backend queues transcription (priority queue since `pablo_edition == "practice"`)
4. Transcription completes → SOAP note generated → status moves to `pending_review`
5. Client displays the SOAP note using the existing session detail UI (with "Practice" badge)

---

## Rate Limiting

| Limit | Default | Configurable Via |
|-------|---------|-----------------|
| Sessions per user per day | 10 | `PRACTICE_DAILY_SESSION_LIMIT` |
| Concurrent sessions per user | 1 | `PRACTICE_MAX_CONCURRENT` |
| Max session duration | 30 minutes | `PRACTICE_MAX_DURATION_MINUTES` |

**Daily limit:** Enforced at `POST /api/practice/sessions`. Returns HTTP `429` with a `Retry-After` header indicating seconds until midnight UTC.

**Concurrent limit:** Enforced at `POST /api/practice/sessions` and at WebSocket `session_start`. A user cannot create a new practice session while another is in `in_progress` status.

**Duration limit:** Enforced server-side. When the limit is reached, the server sends `session_ended` over the WebSocket and closes the Gemini connection. The session transitions to `recording_complete`.

---

## Error Reference

### HTTP Error Responses

Follow the existing platform pattern:

```json
{
  "detail": "Daily practice session limit exceeded (10/10)."
}
```

| HTTP Status | Code | When |
|-------------|------|------|
| `404` | Not Found | Topic ID or session ID not found, or session belongs to another user |
| `409` | Conflict | Session is not in a state that allows the requested action |
| `429` | Too Many Requests | Daily session limit or concurrent session limit exceeded |
| `501` | Not Implemented | Practice mode is disabled (`PRACTICE_ENABLED=false`) |

### WebSocket Close Codes

Application-defined close codes (4000-4999 range):

| Code | Name | When |
|------|------|------|
| `1000` | Normal Close | `session_end` acknowledged, clean shutdown |
| `4001` | Auth Failed | Invalid or expired Firebase token on connect |
| `4002` | Session Not Found | `session_start` or `session_resume` with unknown/expired session ID |
| `4003` | Rate Limited | Concurrent session limit exceeded |
| `4008` | Idle Timeout | No messages received for 10 minutes |
| `4009` | Server Error | Gemini Live connection failed and could not be re-established |

### WebSocket Error Codes (in `error` / `fatal_error` messages)

| Code | Fatal? | Description |
|------|--------|-------------|
| `GEMINI_TIMEOUT` | No | Gemini did not respond within timeout; therapist should try speaking again |
| `GEMINI_CONNECTION_LOST` | Yes | Gemini WebSocket dropped and reconnect with context replay failed |
| `SESSION_EXPIRED` | Yes | Session was cleaned up after disconnection window elapsed |
| `SESSION_DURATION_EXCEEDED` | Yes | Max session duration reached; session ended automatically |
| `INVALID_MESSAGE` | No | Client sent a malformed control message; ignored |

---

## Backend Configuration

New settings added to the existing `Settings` class in `settings.py`:

```python
# Practice Mode
practice_enabled: bool = Field(default=False)
practice_daily_session_limit: int = Field(default=10, ge=1)
practice_max_concurrent: int = Field(default=1, ge=1)
practice_max_duration_minutes: int = Field(default=30, ge=1, le=60)
practice_session_ttl_days: int = Field(default=30, ge=1)
practice_gemini_model: str = Field(default="gemini-2.5-flash-live-001")
practice_gemini_voice: str = Field(default="Kore")
```

---

## Backend File Layout

```
backend/app/
  models/
    practice.py               # NEW: Pydantic request/response models
    enums.py                   # MODIFY: add PRACTICE to SessionSource
  routes/
    practice.py               # NEW: REST endpoints + WebSocket endpoint
  services/
    practice_service.py        # NEW: session lifecycle, topic catalog, state management
    gemini_live_service.py     # NEW: Gemini Live API connection wrapper
  data/
    practice_topics.json       # NEW: topic catalog (static JSON, hot-reloadable in dev)
  settings.py                  # MODIFY: add practice_* settings
  main.py                      # MODIFY: app.include_router(practice.router)
```

---

## Client Implementation Notes

These are not part of the API contract but are included for implementor reference.

### macOS (Swift)

- WebSocket client: `URLSessionWebSocketTask` (built-in, no dependencies)
- Audio playback: `AVAudioPlayer` playing PCM at 24kHz → captured by AudioCaptureKit as system audio
- Mic capture: AudioCaptureKit provides PCM buffers → downsample to 16kHz → send over WebSocket
- Binary frame parsing: `Data` prefix/suffix slicing for the 4-byte header

### Windows (C#, future)

- WebSocket client: `System.Net.WebSockets.ClientWebSocket`
- Audio playback: WASAPI shared mode
- Same WebSocket protocol — no platform-specific messages

### Sequence Diagram: Full Practice Session

```
User clicks "Practice Session"
  │
  ▼
GET /api/practice/topics → show topic picker
  │
  ▼
User picks "Generalized Anxiety"
  │
  ▼
POST /api/practice/sessions {topic_id: "generalized_anxiety"}
  │  → receives session_id, ws_url
  ▼
Start AudioCaptureKit recording (mic + system audio)
  │
  ▼
Open WebSocket to ws_url?token=...
  │  → receive auth_result
  ▼
Send session_start {session_id}
  │  → receive session_started {audio_config}
  ▼
╔═══════════════════════════════════════════╗
║  CONVERSATION LOOP                        ║
║                                           ║
║  Therapist speaks → mic PCM → WebSocket   ║
║  WebSocket → Pablo PCM → AVAudioPlayer    ║
║  AudioCaptureKit captures both channels   ║
║  Repeat for 5-30 minutes                  ║
╚═══════════════════════════════════════════╝
  │
  ▼
User clicks "End Session"
  │
  ▼
Send session_end → receive session_ended
  │
  ▼
Close WebSocket
  │
  ▼
Stop AudioCaptureKit → two audio files
  │
  ▼
POST /api/sessions/{id}/upload-audio (existing endpoint)
  │
  ▼
Pipeline: transcribe → SOAP note → pending_review
  │
  ▼
Show SOAP note with "Practice" badge
```

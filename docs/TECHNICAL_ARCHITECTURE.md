# Technical Architecture

**Project:** Pablo
**Last Updated:** 2026-03-19
**Status:** Active Development

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Principles](#architecture-principles)
3. [Technology Stack](#technology-stack)
4. [Backend Architecture](#backend-architecture)
5. [Frontend Architecture](#frontend-architecture)
6. [Data Model](#data-model)
7. [API Design](#api-design)
8. [Security & HIPAA Compliance](#security--hipaa-compliance)
9. [Deployment Architecture](#deployment-architecture)
10. [Development Workflow](#development-workflow)
11. [Testing Strategy](#testing-strategy)
12. [Performance & Scalability](#performance--scalability)

---

## System Overview

### Purpose

A HIPAA-compliant web application that helps therapists generate SOAP notes from therapy session transcripts using AI-powered analysis.

### Core Functionality

1. **Patient Management:** Therapists manage patient records (CRUD operations)
2. **Session Upload:** Upload therapy session transcripts (VTT, JSON, or TXT)
3. **SOAP Generation:** AI generates SOAP notes (Subjective, Objective, Assessment, Plan)
4. **Review & Edit:** Therapists review, edit, and finalize SOAP notes
5. **Quality Tracking:** Rate SOAP quality (1-5 stars) for continuous improvement
6. **Data Export:** Export patient data and SOAP notes (PDF/JSON)

### User Roles

- **Therapist:** Primary user - manages patients, uploads sessions, reviews SOAP notes
- **Admin:** (Future) - manages system, compliance reports, user management

### Architecture Style

- **Frontend:** Single-page application (SPA) with Next.js App Router
- **Backend:** RESTful API with FastAPI
- **Data:** Relational (PostgreSQL via Cloud SQL, SQLAlchemy + Alembic)
- **Auth:** Firebase Authentication with JWT tokens
- **AI:** Meeting-transcription pipeline with mental health plugin

---

## Architecture Principles

### 1. Senior Engineering Principles

**From CLAUDE.md:**

- **Quality over speed** - Clean, modern, readable code
- **DRY (Don't Repeat Yourself)** - Extract common patterns
- **Self-documenting code** - Clear names eliminate most comments
- **Modern best practices** - Latest language features and patterns
- **No compromises** - If there's a cleaner way, do it that way

### 2. Security First

- **Multi-tenant isolation** - User data strictly partitioned by `user_id`
- **HIPAA compliance** - PHI protected at rest and in transit
- **Zero trust** - Validate auth on every request
- **No PHI in logs** - Structured error codes, no patient data in logs

### 3. Separation of Concerns

- **Repository pattern** - Data access abstracted from business logic
- **Service layer** - Business logic separated from API routes
- **Type safety** - Full TypeScript frontend, strict mypy backend
- **Clear boundaries** - Each module has single responsibility

### 4. Developer Experience

- **Dev mode support** - Mock data for offline development
- **Hot reload** - Docker Compose with volume mounts
- **Clear errors** - Structured error codes with user-friendly messages
- **Comprehensive types** - TypeScript/Python type annotations

---

## Technology Stack

### Frontend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Framework** | Next.js | 15.4.10 | React framework with App Router |
| **UI Library** | React | 19.2.3 | Component-based UI |
| **Language** | TypeScript | 5.9.3 | Type-safe JavaScript |
| **Styling** | Tailwind CSS | 4.1.18 | Utility-first CSS |
| **Auth** | NextAuth.js | 5.0.0-beta.30 | Authentication flows |
| **Icons** | lucide-react | Latest | Consistent icon library |
| **HTTP Client** | Fetch API | Native | API communication |
| **PDF Generation** | jsPDF | 2.5.1 | SOAP note PDF export |
| **Testing Framework** | Vitest | 4.0.16 | Unit and component testing |
| **E2E Testing** | Playwright | 1.57.0 | Browser automation and testing |
| **Data Fetching** | React Query | 5.90.16 | Server state management |

### Backend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Framework** | FastAPI | 0.115.0 | Async Python web framework |
| **Language** | Python | 3.13+ | Modern Python with type hints |
| **Database** | Firestore | GCP | NoSQL document database |
| **Auth** | Firebase Auth | GCP | User authentication |
| **Storage** | Cloud Storage | GCP | File storage (future) |
| **AI Pipeline** | meeting-transcription | Internal | SOAP generation pipeline |
| **Validation** | Pydantic | 2.5.0 | Request/response validation |
| **Type Checking** | mypy | Latest | Static type analysis |
| **Linting** | Ruff | Latest | Fast Python linter |
| **Testing** | pytest | Latest | Unit and integration tests |

### Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Hosting** | Google Cloud Run | Serverless container hosting |
| **Database** | Firestore (Native Mode) | Production database |
| **Authentication** | Firebase Authentication | User management |
| **Secrets** | Secret Manager | API keys and credentials |
| **Logging** | Cloud Logging | Centralized logs |
| **Audit** | Cloud Audit Logs | HIPAA compliance logging |
| **CI/CD** | GitHub Actions | Automated testing and deployment |
| **Local Dev** | Docker Compose | Local development environment |

---

## Backend Architecture

### Directory Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app entry point
│   ├── auth/
│   │   ├── __init__.py
│   │   └── service.py             # Firebase auth integration
│   ├── database.py                # Firestore client singleton
│   ├── middleware.py              # HTTPS redirect, CORS
│   ├── models/
│   │   ├── __init__.py
│   │   ├── patient.py             # Patient dataclass
│   │   ├── session.py             # TherapySession dataclass
│   │   └── user.py                # User dataclass
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── patient_repository.py  # Patient CRUD interface
│   │   ├── session_repository.py  # Session CRUD interface
│   │   └── user_repository.py     # User CRUD interface
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── patients.py            # Patient API endpoints
│   │   ├── sessions.py            # Session API endpoints
│   │   └── users.py               # User API endpoints
│   ├── services/
│   │   ├── __init__.py
│   │   ├── export_service.py      # Patient data export (PDF/JSON)
│   │   └── soap_generation_service.py  # SOAP note generation
│   └── settings.py                # Environment-based config
├── plugins/
│   └── mental_health/
│       ├── __init__.py
│       ├── plugin.py               # Plugin interface
│       └── mental_health_plugin.py # SOAP generation logic
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # Pytest fixtures
│   ├── test_patients.py           # Patient API tests
│   ├── test_sessions.py           # Session API tests
│   └── test_integration.py        # End-to-end tests
├── Dockerfile                     # Production container
├── pyproject.toml                 # Poetry dependencies
└── pytest.ini                     # Test configuration
```

### Layered Architecture

```
┌─────────────────────────────────────────┐
│         API Routes Layer                │
│  (patients.py, sessions.py, users.py)   │
│  - Request validation (Pydantic)        │
│  - Response formatting                  │
│  - HTTP status codes                    │
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│        Service Layer                    │
│  (soap_generation_service.py, etc.)     │
│  - Business logic                       │
│  - AI pipeline integration              │
│  - Data transformations                 │
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│      Repository Layer                   │
│  (patient_repository.py, etc.)          │
│  - Data access abstraction              │
│  - Firestore queries                    │
│  - Multi-tenant filtering               │
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│         Database Layer                  │
│  (Firestore)                            │
│  - Document storage                     │
│  - Automatic indexing                   │
│  - Encryption at rest                   │
└─────────────────────────────────────────┘
```

### Repository Pattern

**Purpose:** Abstract data access from business logic

**Interface:**
```python
class PatientRepository(ABC):
    @abstractmethod
    def create(self, patient: Patient) -> Patient: ...

    @abstractmethod
    def get(self, patient_id: str, user_id: str) -> Patient | None: ...

    @abstractmethod
    def list(self, user_id: str, search: str | None) -> list[Patient]: ...

    @abstractmethod
    def update(self, patient: Patient) -> Patient: ...

    @abstractmethod
    def delete(self, patient_id: str, user_id: str) -> None: ...
```

**Implementations:**
- `FirestorePatientRepository` - Production (uses Firestore)
- `InMemoryPatientRepository` - Testing (in-memory dictionary)

**Benefits:**
- Easy to mock for testing
- Swap data source without changing business logic
- Multi-tenant filtering enforced at repository level

### Service Layer Pattern

**Purpose:** Encapsulate business logic

**Example: SOAP Generation Service**

```python
class SOAPGenerationService(ABC):
    @abstractmethod
    def generate_soap_note(
        self,
        transcript: Transcript,
        patient: Patient,
        session_date: str
    ) -> SOAPNote: ...
```

**Implementations:**
- `MeetingTranscriptionSOAPService` - Real AI generation
- `MockSOAPGenerationService` - Testing (deterministic output)

**Benefits:**
- Swap AI providers without changing API
- Easy to test with mock service
- Clear separation of concerns

### Authentication Flow

```
1. User logs in via Google OAuth (NextAuth)
   ↓
2. Firebase Auth creates user account
   ↓
3. Frontend receives Firebase ID token (JWT)
   ↓
4. Frontend includes token in Authorization header
   ↓
5. Backend middleware verifies token signature
   ↓
6. Backend extracts user_id from JWT claims
   ↓
7. Dependency injection provides User object to routes
```

**Key Function:**
```python
async def get_current_user(
    authorization: str = Header(None)
) -> User:
    """Extract and validate user from JWT token."""
    # Verify Firebase token
    # Extract user_id from claims
    # Fetch user from Firestore
    # Return User object
```

### Local Development Authentication

**Purpose:** Simplify local development without requiring Google OAuth setup.

**Configuration:**
- Only available when `ENVIRONMENT=development` and `ENABLE_LOCAL_AUTH=true`
- Never enabled in production (safety check in code)

**Flow:**
```
1. Developer enters credentials in login form
   ↓
2. NextAuth Credentials provider calls /api/auth/local/login
   ↓
3. LocalAuthService validates credentials (dev@example.com/password)
   ↓
4. Backend returns JWT token matching Firebase token structure
   ↓
5. Frontend stores token in NextAuth session
   ↓
6. Token used for API authentication (same as production)
```

**Test Credentials:**
- Email: `dev@example.com`
- Password: `password`

**Implementation:**
- Backend: `backend/app/auth/local.py` - LocalAuthService
- Routes: `backend/app/routes/auth.py` - `/api/auth/local/login` endpoint
- Frontend: `frontend/src/lib/auth.ts` - NextAuth Credentials provider

**Security:**
- Production check prevents accidental enablement
- JWT tokens use HS256 signing with `JWT_SECRET_KEY`
- Tokens include same claims as Firebase tokens (user_id, email)

### Multi-Tenant Isolation

**Every query filters by `user_id`:**

```python
def get(self, patient_id: str, user_id: str) -> Patient | None:
    """Get patient - CRITICAL: filter by user_id for multi-tenant security."""
    doc = self.db.collection("patients").document(patient_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()

    # SECURITY: Verify patient belongs to requesting user
    if data["user_id"] != user_id:
        return None  # Pretend it doesn't exist

    return Patient(**data)
```

**Rule:** Never expose data across user boundaries. Treat missing patient and unauthorized patient identically (both return 404).

---

## Frontend Architecture

### Directory Structure

```
frontend/
├── app/
│   ├── (dashboard)/               # Route group (shared layout)
│   │   ├── dashboard/
│   │   │   ├── layout.tsx         # Dashboard layout (auth check)
│   │   │   ├── page.tsx           # Dashboard home
│   │   │   ├── patients/
│   │   │   │   ├── page.tsx       # Patient list
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx   # Patient detail
│   │   │   └── sessions/
│   │   │       ├── page.tsx       # Sessions list
│   │   │       └── [id]/
│   │   │           └── page.tsx   # Session detail
│   ├── api/
│   │   └── auth/                  # NextAuth API routes
│   ├── baa-acceptance/
│   │   └── page.tsx               # BAA acceptance flow
│   ├── login/
│   │   └── page.tsx               # Login page
│   ├── globals.css                # Tailwind styles
│   ├── layout.tsx                 # Root layout
│   └── page.tsx                   # Landing page
├── src/
│   ├── components/
│   │   ├── baa/
│   │   │   ├── BAAAcceptanceForm.tsx
│   │   │   └── BAATextDisplay.tsx
│   │   ├── layout/
│   │   │   ├── Header.tsx
│   │   │   └── Sidebar.tsx
│   │   ├── patients/
│   │   │   ├── PatientExport.tsx
│   │   │   ├── PatientForm.tsx    # (To be added)
│   │   │   └── PatientTable.tsx   # (To be added)
│   │   ├── sessions/
│   │   │   ├── UploadTranscriptDialog.tsx  # (To be added)
│   │   │   ├── SOAPViewer.tsx     # (To be added)
│   │   │   └── ...
│   │   └── ui/
│   │       └── Toast.tsx
│   ├── lib/
│   │   ├── api/
│   │   │   ├── client.ts          # Generic API client
│   │   │   ├── users.ts           # User endpoints
│   │   │   ├── patients.ts        # (To be added)
│   │   │   └── sessions.ts        # (To be added)
│   │   ├── errors/
│   │   │   └── api-errors.ts      # Error handling
│   │   ├── config.ts              # Environment config
│   │   └── mockData.ts            # Dev mode mock data
│   └── types/
│       ├── baa.ts
│       ├── patients.ts            # (To be added)
│       └── sessions.ts            # (To be added)
├── public/
│   └── images/
├── .env.local                     # Local environment vars
├── next.config.js
├── package.json
├── tailwind.config.ts
└── tsconfig.json
```

### Component Architecture Patterns

#### 1. API Client Pattern

**Generic type-safe client:**

```typescript
// Generic API client
export async function apiClient<T>(
  endpoint: string,
  options: FetchOptions = {}
): Promise<T> {
  const baseUrl = API_URL
  const url = `${baseUrl}${endpoint}`

  const response = await fetch(url, {
    method: options.method || "GET",
    headers: {
      "Content-Type": "application/json",
      ...(options.token && { Authorization: `Bearer ${options.token}` }),
      ...options.headers,
    },
    body: options.body,
  })

  if (!response.ok) {
    throw new ApiError(/* parse error response */)
  }

  return response.json()
}

// Helper methods
export async function get<T>(endpoint: string, token?: string): Promise<T>
export async function post<T>(endpoint: string, data: unknown, token?: string): Promise<T>
export async function patch<T>(endpoint: string, data: unknown, token?: string): Promise<T>
export async function del<T>(endpoint: string, token?: string): Promise<T>
```

**Usage:**
```typescript
// In api/patients.ts
export async function listPatients(token?: string): Promise<PatientListResponse> {
  return get<PatientListResponse>("/api/patients", token)
}
```

#### 2. Form Handling Pattern (Manual Validation)

**State management:**
```typescript
const [formData, setFormData] = useState<FormDataType>({...})
const [errors, setErrors] = useState<FormErrorsType>({})
const [isSubmitting, setIsSubmitting] = useState(false)
```

**Field validation:**
```typescript
const validateField = (name: keyof FormDataType, value: string): string | undefined => {
  switch (name) {
    case "first_name":
      if (!value.trim()) return "First name is required"
      if (value.length > 255) return "First name must be 255 characters or less"
      break
    // ... other fields
  }
  return undefined
}
```

**Change handler:**
```typescript
const handleChange = (name: keyof FormDataType, value: string) => {
  setFormData(prev => ({ ...prev, [name]: value }))
  setErrors(prev => ({ ...prev, [name]: undefined }))  // Clear error on change
}
```

**Blur handler:**
```typescript
const handleBlur = (name: keyof FormDataType) => {
  const error = validateField(name, formData[name])
  if (error) {
    setErrors(prev => ({ ...prev, [name]: error }))
  }
}
```

**Submit handler:**
```typescript
const handleSubmit = async (e: React.FormEvent) => {
  e.preventDefault()

  if (!validateForm()) {
    showToast("Please fix the errors in the form", "error")
    return
  }

  setIsSubmitting(true)
  try {
    const result = await createPatient(formData, token)
    showToast("Success!", "success")
    onSuccess(result)
  } catch (error) {
    // Map backend validation errors to form fields
    const validationErrors = getValidationErrors(error)
    if (validationErrors) {
      setErrors(validationErrors)
    } else {
      showToast(getUserErrorMessage(error), "error")
    }
  } finally {
    setIsSubmitting(false)
  }
}
```

#### 3. Error Handling Pattern

**Structured error codes:**
```typescript
export const ErrorCodes = {
  BAA_NOT_ACCEPTED: "BAA_NOT_ACCEPTED",
  UNAUTHORIZED: "UNAUTHORIZED",
  FORBIDDEN: "FORBIDDEN",
  VALIDATION_ERROR: "VALIDATION_ERROR",
  NOT_FOUND: "NOT_FOUND",
  INTERNAL_SERVER_ERROR: "INTERNAL_SERVER_ERROR",
  NETWORK_ERROR: "NETWORK_ERROR",
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public details?: Record<string, any>,
    public statusCode?: number
  ) {
    super(message)
    this.name = "ApiError"
  }
}
```

**Helper functions:**
```typescript
// Check if error is ApiError
export function isApiError(error: unknown): error is ApiError

// Check for specific error code
export function hasErrorCode(error: unknown, code: string): boolean

// Get user-friendly message
export function getUserErrorMessage(error: unknown): string

// Extract validation errors for form fields
export function getValidationErrors(error: unknown): Record<string, string> | null
```

#### 4. Toast Notification Pattern

**Global toast system:**
```typescript
// Toast.tsx exports a global function
export function showToast(message: string, type: "error" | "success" | "info" | "warning") {
  // Add toast to global state
}

// Usage anywhere
import { showToast } from "@/components/ui/Toast"
showToast("Patient created successfully", "success")
```

#### 5. Dev Mode Pattern

**Environment config:**
```typescript
// lib/config.ts
export const IS_DEV_MODE = process.env.NEXT_PUBLIC_DEV_MODE === "true"
export const DATA_MODE = process.env.NEXT_PUBLIC_DATA_MODE || "mock"
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

export const useMockData = () => IS_DEV_MODE || DATA_MODE === "mock"
```

**Conditional API calls:**
```typescript
export async function listPatients(token?: string): Promise<PatientListResponse> {
  if (IS_DEV_MODE) {
    await new Promise(resolve => setTimeout(resolve, 1000))  // Simulate delay
    return {
      data: mockPatients,
      total: mockPatients.length,
      page: 1,
      page_size: mockPatients.length
    }
  }
  return get<PatientListResponse>("/api/patients", token)
}
```

### Design System

**Color Palette (Calming Therapeutic):**

```typescript
// tailwind.config.ts
export default {
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#E3F2FD',
          // ... through ...
          900: '#0D47A1',
        },
        secondary: {
          50: '#E0F2F1',
          // ... teal shades ...
          900: '#004D40',
        },
        accent: {
          50: '#F1F8F9',
          // ... sage green shades ...
          900: '#3E6B6F',
        },
        neutral: {
          50: '#F5F5F0',
          // ... warm gray shades ...
          900: '#1A1A1A',
        },
      }
    }
  }
}
```

**Component Classes (globals.css):**

```css
.btn-primary {
  @apply bg-primary-600 text-white px-6 py-3 rounded-lg
         font-medium hover:bg-primary-700 hover:shadow-md
         transition-all;
}

.card {
  @apply bg-white rounded-xl shadow-sm hover:shadow-md
         transition-shadow p-6;
}

.input {
  @apply w-full px-4 py-3 border border-neutral-300
         rounded-lg focus:outline-none focus:ring-2
         focus:ring-primary-500;
}
```

**Typography:**

- **Display (headings):** Poppins (600-700 weight)
- **Body:** Inter (400-500 weight)

---

## Data Model

### Collections

#### patients

```json
{
  "id": "uuid",
  "user_id": "firebase_uid",
  "first_name": "string",
  "last_name": "string",
  "date_of_birth": "ISO8601 | null",
  "diagnosis": "string | null",
  "session_count": "number",
  "last_session_date": "ISO8601 | null",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Indexes:**
- `user_id` (automatic)
- `user_id + last_name_lower` (composite for sorting)
- `user_id + first_name_lower` (composite for search)

#### sessions

```json
{
  "id": "uuid",
  "user_id": "firebase_uid",
  "patient_id": "uuid",
  "session_date": "ISO8601",
  "session_number": "number",
  "status": "queued | processing | pending_review | finalized | failed",
  "transcript": {
    "format": "vtt | json | txt",
    "content": "string"
  },
  "soap_note": {
    "subjective": "string",
    "objective": "string",
    "assessment": "string",
    "plan": "string"
  } | null,
  "soap_note_edited": {
    "subjective": "string",
    "objective": "string",
    "assessment": "string",
    "plan": "string"
  } | null,
  "quality_rating": "number (1-5) | null",
  "created_at": "ISO8601",
  "processing_started_at": "ISO8601 | null",
  "processing_completed_at": "ISO8601 | null",
  "finalized_at": "ISO8601 | null",
  "error": "string | null"
}
```

**Indexes:**
- `user_id` (automatic)
- `patient_id + session_date` (composite for patient session list)
- `user_id + status` (composite for filtering)

#### users

```json
{
  "id": "firebase_uid",
  "email": "string",
  "display_name": "string | null",
  "baa_accepted_at": "ISO8601 | null",
  "baa_version": "string | null",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Indexes:**
- `id` (automatic - document ID)
- `email` (automatic)

### Data Relationships

```
users (1) ────────────> (*) patients
                           │
                           └────────> (*) sessions
```

**Rules:**
- One user has many patients
- One patient has many sessions
- All queries filtered by `user_id` for multi-tenant isolation
- Sessions reference `patient_id` for joining

### Data Flow: Session Upload

```
1. User uploads transcript file (VTT/JSON/TXT)
   ↓
2. Frontend reads file content
   ↓
3. POST /api/patients/{id}/sessions/upload
   ↓
4. Backend creates session (status="queued")
   ↓
5. Backend starts SOAP generation (status="processing")
   ↓
6. AI pipeline processes transcript
   ↓
7. Backend saves SOAP note (status="pending_review")
   ↓
8. Frontend polls GET /api/sessions/{id} until complete
   ↓
9. Therapist reviews/edits SOAP note
   ↓
10. PATCH /api/sessions/{id}/finalize (status="finalized")
```

---

## API Design

### REST Principles

- **Resource-based URLs:** `/api/patients`, `/api/sessions`
- **HTTP methods:** GET (read), POST (create), PATCH (update), DELETE (remove)
- **Status codes:** 200 (OK), 201 (Created), 400 (Bad Request), 401 (Unauthorized), 404 (Not Found), 500 (Server Error)
- **JSON:** All request/response bodies are JSON
- **Pagination:** (Future) `page`, `page_size`, `total` in list responses

### Authentication

**Header:**
```
Authorization: Bearer <firebase_jwt_token>
```

**Backend validates token on every request:**
- Verifies signature with Firebase
- Extracts `user_id` from claims
- Returns 401 if invalid/missing token

### Endpoint Patterns

#### List Resources

```
GET /api/patients?search=smith&search_by=last_name

Response:
{
  "data": [...],
  "total": 10,
  "page": 1,
  "page_size": 10
}

Error (invalid search_by):
422 Unprocessable Entity
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "search_by must be one of: first_name, last_name"
  }
}
```

#### Get Single Resource

```
GET /api/patients/{patient_id}

Response:
{
  "id": "...",
  "user_id": "...",
  "first_name": "...",
  ...
}
```

#### Create Resource

```
POST /api/patients

Body:
{
  "first_name": "John",
  "last_name": "Doe",
  "date_of_birth": "1990-01-01",
  "diagnosis": "Anxiety"
}

Response: 201 Created
{
  "id": "...",
  ...
}
```

#### Update Resource

```
PATCH /api/patients/{patient_id}

Body:
{
  "diagnosis": "Depression and Anxiety"
}

Response: 200 OK
{
  "id": "...",
  ...
}
```

#### Delete Resource

```
DELETE /api/patients/{patient_id}

Response: 200 OK
{
  "message": "Patient deleted successfully"
}
```

### Error Response Format

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Validation failed",
    "details": {
      "first_name": "First name is required"
    }
  }
}
```

### Validation Rules

**Patient:**
- `first_name`: required, max 255 chars
- `last_name`: required, max 255 chars
- `date_of_birth`: optional, ISO 8601 format
- `diagnosis`: optional, text

**Patient Search:**
- `search`: optional, text query
- `search_by`: optional, enum ["first_name", "last_name"]
- Invalid `search_by` values return 422 Unprocessable Entity

**Session Upload:**
- `session_date`: required, ISO 8601 datetime
- `transcript.format`: required, one of ["vtt", "json", "txt"]
- `transcript.content`: required, non-empty string

**Session Finalize:**
- `quality_rating`: required, integer 1-5
- `soap_note_edited`: optional, 4 sections (S, O, A, P)

### Input Validation Strategy

**Philosophy:** Validate at boundaries, trust internal code.

**Validation Layers:**

1. **Type Validation (Pydantic Models)**
   - Request/response bodies
   - Field types and required/optional
   - Example: `first_name: str = Field(min_length=1, max_length=255)`

2. **Enum Validation (Python Enums)**
   - Constrained string values
   - Example: `PatientSearchField(str, Enum)` with `FIRST_NAME = "first_name"`
   - Benefits: IDE autocomplete, compile-time checking, API documentation

3. **Business Logic Validation (Service Layer)**
   - BAA acceptance before PHI access
   - Multi-tenant isolation (user_id checks)
   - Resource ownership verification

**Example Pattern:**
```python
# models.py
class PatientSearchField(str, Enum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"

# routes.py
@router.get("/api/patients")
def list_patients(
    search: str | None = None,
    search_by: PatientSearchField | None = None,  # Auto-validated by FastAPI
    current_user: User = Depends(get_current_user)
):
    # search_by is guaranteed to be valid here
    ...
```

**Benefits:**
- Centralized validation logic
- Self-documenting API (OpenAPI schema shows valid values)
- Type-safe throughout codebase
- Clear error messages to users

---

## Security & HIPAA Compliance

### Encryption

**In Transit:**
- HTTPS enforced (middleware redirects HTTP → HTTPS)
- TLS 1.2+ required
- HSTS headers configured

**At Rest:**
- Firestore: AES-256 encryption (automatic)
- Firebase Auth: Encrypted user credentials
- Backups: Encrypted automatically

### Authentication & Authorization

**Authentication:**
- Firebase Authentication (Google OAuth)
- JWT tokens with short expiration
- Token verification on every request

**Authorization:**
- Multi-tenant isolation: All queries filter by `user_id`
- BAA enforcement: Middleware checks `baa_accepted_at` before PHI access
- Resource ownership: Verify `user_id` matches resource owner

**Development Authentication:**
- Local username/password auth available for development
- Controlled by `ENABLE_LOCAL_AUTH` environment variable
- Production check prevents accidental enablement
- Uses same JWT structure as Firebase tokens for consistency

**Session Management:**
- (Future) Automatic timeout after 15-30 minutes inactivity
- (Future) Refresh token rotation

### HIPAA Requirements

**Access Control (§ 164.312(a)):**
- ✅ Unique user identification (Firebase UID)
- ✅ Emergency access procedure (admin override - future)
- ⚠️ Automatic logoff (planned - THERAPY-4vr)
- ⚠️ Encryption and decryption (at rest ✅, in transit ✅)

**Audit Controls (§ 164.312(b)):**
- ✅ Cloud Audit Logs enabled (Firestore DATA_WRITE, ADMIN_READ)
- ⚠️ Application-level audit logging (planned - THERAPY-960)
- ⚠️ 6-year log retention (planned - THERAPY-6cg)

**Integrity (§ 164.312(c)):**
- ✅ Authenticate electronic protected health information
- ✅ Mechanism to verify PHI not altered (Firestore versioning)

**Transmission Security (§ 164.312(e)):**
- ✅ Integrity controls (HTTPS, TLS)
- ✅ Encryption (TLS 1.2+)

**Business Associate Agreements:**
- ✅ User BAA acceptance required (THERAPY-alj, THERAPY-70k)
- ✅ Google Cloud BAA signed (THERAPY-5mx)

**Data Retention:**
- ⚠️ Retention policy (planned - THERAPY-5ae)
- ⚠️ Secure deletion (planned)

### Security Best Practices

**Input Validation:**
- Pydantic models validate all requests
- Max field lengths enforced
- Type checking (mypy, TypeScript)

**Error Handling:**
- Never expose PHI in error messages
- Generic error messages to users
- Detailed errors logged server-side only
- Structured error codes (no stack traces to users)

**Logging:**
- Never log PHI (patient names, session content)
- Log user_id and resource IDs only
- Structured logging with severity levels
- Separate application logs from audit logs

**Rate Limiting:**
- (Planned - THERAPY-a3q) Prevent DoS attacks
- 100 req/min read, 20 req/min write

**CORS:**
- Restricted origins (environment-based)
- Limited methods (GET, POST, PATCH, DELETE)
- Credentials allowed only for trusted origins

---

## Deployment Architecture

### Local Development

**Docker Compose Services:**

```yaml
services:
  frontend:
    - Port: 3000
    - Volume: ./frontend (hot reload)
    - Environment: NEXT_PUBLIC_DEV_MODE=true

  backend:
    - Port: 8000
    - Volume: ./backend (hot reload)
    - Dockerfile: backend/Dockerfile.dev
    - Environment: ENABLE_LOCAL_AUTH=true, ENVIRONMENT=development
    - GCloud credentials mounted for Vertex AI access

  firebase:
    - Firestore Emulator: 8080
    - Auth Emulator: 9099
    - Storage Emulator: 9199
    - Emulator UI: 4000
    - Persistent data: ./firebase-data
```

**Access Points:**
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000/docs (OpenAPI)
- Firebase Emulator UI: http://localhost:4000

**Features:**
- Hot reload for frontend and backend
- Firebase emulators for offline development
- Local authentication (no Google OAuth needed)
- GCP credentials for Vertex AI (Gemini) access

### Production (Google Cloud Platform)

```
┌──────────────────────────────────────────────┐
│             Cloud Load Balancer              │
│                 (HTTPS)                      │
└───────────┬──────────────────────────────────┘
            │
            ├──> Frontend (Cloud Run)
            │    - Next.js SSR
            │    - Auto-scaling
            │    - CDN caching
            │
            └──> Backend (Cloud Run)
                 - FastAPI
                 - Auto-scaling
                 - Max 1000 instances

┌──────────────────────────────────────────────┐
│          Firebase Authentication             │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│       Firestore (Native Mode)                │
│       - Multi-region replication             │
│       - Point-in-time recovery               │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│            Secret Manager                    │
│            - API keys                        │
│            - Firebase credentials            │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│            Cloud Logging                     │
│            - Application logs                │
│            - Audit logs (6-year retention)   │
└──────────────────────────────────────────────┘
```

### Deployment Process

**Manual (Current):**
1. Build Docker images locally
2. Push to Google Container Registry
3. Deploy to Cloud Run via `gcloud` CLI

**Automated (Planned - THERAPY-6lc):**
```yaml
# .github/workflows/deploy.yml

on:
  push:
    branches: [main]

jobs:
  test:
    - Run linting (ruff, mypy)
    - Run tests (pytest)
    - Run frontend tests (jest)

  build:
    - Build backend Docker image
    - Build frontend Docker image
    - Push to GCR

  deploy:
    - Deploy backend to Cloud Run
    - Deploy frontend to Cloud Run
    - Run smoke tests
```

### Environment Configuration

#### Environment Variables Reference

**Core Configuration:**

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ENVIRONMENT` | development\|production | development | Environment mode |
| `GCP_PROJECT_ID` | string | - | Google Cloud project ID |
| `FIREBASE_PROJECT_ID` | string | - | Firebase project ID |

**Authentication:**

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ENABLE_LOCAL_AUTH` | boolean | false | Enable local dev authentication (dev only) |
| `JWT_SECRET_KEY` | string | dev-secret-key | JWT signing key (dev mode only) |
| `JWT_ALGORITHM` | string | HS256 | JWT signing algorithm |

**AI Configuration:**

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANTHROPIC_API_KEY` | string | - | Anthropic API key for Claude |
| `AI_MODEL` | string | google:gemini-3.1-pro-preview | Vertex AI model selection |
| `GOOGLE_REGION` | string | global | GCP region for Vertex AI |

**Firebase Emulators (Development Only):**

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `FIRESTORE_EMULATOR_HOST` | string | - | e.g., firebase:8080 |
| `FIREBASE_AUTH_EMULATOR_HOST` | string | - | e.g., firebase:9099 |
| `FIREBASE_STORAGE_EMULATOR_HOST` | string | - | e.g., firebase:9199 |

**Frontend Configuration:**

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `NEXT_PUBLIC_DEV_MODE` | boolean | false | Enable dev mode features |
| `NEXT_PUBLIC_DATA_MODE` | mock\|api | api | Data source (mock for offline) |
| `NEXT_PUBLIC_API_URL` | string | http://localhost:8000 | Backend API base URL |
| `NEXTAUTH_URL` | string | http://localhost:3000 | NextAuth base URL |
| `NEXTAUTH_SECRET` | string | - | NextAuth session encryption key |
| `GOOGLE_CLIENT_ID` | string | - | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | string | - | Google OAuth client secret |

**CORS & Security:**

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CORS_ORIGINS` | JSON array | ["http://localhost:3000"] | Allowed CORS origins |

---

## Development Workflow

### Local Setup

```bash
# 1. Clone repository
git clone https://github.com/pablo-health/pablo.git
cd pablo

# 2. Start Docker Compose
docker-compose up

# 3. Access application
# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
# Firebase Emulator UI: http://localhost:4000
```

### Code Quality Tools

**Backend:**
```bash
# Linting
make lint          # Run ruff + mypy
make format        # Auto-fix formatting

# Testing
make test          # Run pytest
make test-cov      # With coverage report

# All checks
make check         # Lint + test
```

**Frontend:**
```bash
# Linting
npm run lint       # ESLint

# Type checking
npm run type-check # TypeScript

# Testing
npm run test       # Jest
```

### Git Workflow

**Branch Strategy:**
```
main (protected)
  └─> THERAPY-xxx-feature-name (feature branch)
```

**Process:**
1. Create branch: `git checkout -b THERAPY-xxx-short-description`
2. Make changes
3. Commit: `git commit -m "feat: Add feature description"`
4. Push: `git push -u origin THERAPY-xxx-short-description`
5. Create PR to `main`
6. Code review
7. Merge after CI passes

**PR Requirements:**
- All tests passing
- Linting clean (ruff, mypy, ESLint)
- Code review approved
- Beads issue referenced

### Beads Issue Tracking

**Workflow:**
```bash
# 1. Find work
bd ready

# 2. Claim task
bd update THERAPY-xxx --status=in_progress

# 3. Do work
git checkout -b THERAPY-xxx-feature-name

# 4. Complete task
bd close THERAPY-xxx --reason="Implemented. See PR #42"
```

---

## Testing Strategy

### Backend Testing

**Unit Tests (pytest):**
- Test individual functions/methods
- Mock external dependencies (Firestore, Firebase Auth)
- Fast, isolated

**Example:**
```python
def test_create_patient(mock_firestore):
    repo = FirestorePatientRepository(mock_firestore)
    patient = Patient(
        id="p1",
        user_id="u1",
        first_name="John",
        last_name="Doe",
        ...
    )
    result = repo.create(patient)
    assert result.id == "p1"
```

**Integration Tests:**
- Test API endpoints end-to-end
- Use TestClient (FastAPI)
- Use Firebase emulator

**Example:**
```python
def test_create_patient_endpoint(client, auth_token):
    response = client.post(
        "/api/patients",
        json={"first_name": "John", "last_name": "Doe"},
        headers={"Authorization": f"Bearer {auth_token}"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["first_name"] == "John"
```

**Test Pyramid:**
```
     ┌─────────┐
     │  E2E    │  (10% - Full flow)
     ├─────────┤
    │Integration│ (30% - API endpoints)
    ├───────────┤
   │  Unit Tests │ (60% - Business logic)
   └─────────────┘
```

### Frontend Testing

**Test Framework:**
- **Vitest** (not Jest) - Fast, modern test runner with native ESM support
- **React Testing Library** - User-centric component testing
- **Playwright** - E2E browser automation

**Component Tests (Vitest + React Testing Library):**
- Test user interactions
- Test validation
- Test error handling
- Requires React Query wrapper for components using data fetching

**Example:**
```typescript
import { renderWithQueryClient } from '@/test/utils'

it("displays sessions for selected patient", async () => {
  // renderWithQueryClient wraps component with QueryClientProvider
  renderWithQueryClient(<SessionsPage patientId="p1" />)

  // Test loading state
  expect(screen.getByText(/loading/i)).toBeInTheDocument()

  // Wait for data
  await waitFor(() => {
    expect(screen.getByText("Session 1")).toBeInTheDocument()
  })
})
```

**Test Patterns:**
- Mock child components for isolation (e.g., mock UploadTranscriptDialog)
- Test behavior (loading, error, success states)
- Use React Query test utilities for data fetching
- Mock API calls with vi.mock()

**E2E Tests (Playwright):**
- Full user journeys
- Browser compatibility testing
- Visual regression (screenshots)
- Network request validation

**Manual Testing:**
- Full user flows (create patient → upload → review → finalize)
- Responsive design (mobile, tablet, desktop)
- Browser compatibility (Chrome, Firefox, Safari)
- HIPAA compliance (no PHI in network tab, console)

### Test Coverage Goals

- Backend: >80% line coverage
- Frontend: >70% line coverage
- Critical paths: 100% coverage (auth, multi-tenant, SOAP generation)

---

## Performance & Scalability

### Current Bottlenecks

1. **Synchronous SOAP generation** - Blocks request for 5-30 seconds
   - Mitigation (planned - THERAPY-hl0): Async processing with Cloud Tasks

2. **No caching** - Refetch data on every page load
   - Mitigation (future): Add React Query or SWR

3. **No pagination** - Load all patients/sessions at once
   - Mitigation (future): Implement cursor-based pagination

### Scalability Targets

**MVP (3-5 therapists):**
- 100 patients
- 1,000 sessions
- 10 concurrent users
- Current architecture sufficient

**Growth (100 therapists):**
- 10,000 patients
- 100,000 sessions
- 500 concurrent users
- Need async SOAP, pagination, caching

**Scale (1,000+ therapists):**
- 100,000+ patients
- 1,000,000+ sessions
- 5,000+ concurrent users
- Need CDN, read replicas, batch processing

### Performance Optimizations

**Backend:**
- Connection pooling (Firestore client reuse)
- Async endpoints (FastAPI native)
- Background jobs for SOAP (planned)
- Firestore composite indexes

**Frontend:**
- Code splitting (Next.js automatic)
- Image optimization (Next.js automatic)
- Static generation where possible
- Lazy loading components

**Database:**
- Proper indexing (user_id, patient_id, session_date)
- Denormalization where needed (patient_name in sessions)
- TTL for temporary data (future)

---

## Appendix

### Glossary

- **BAA:** Business Associate Agreement (HIPAA requirement)
- **HIPAA:** Health Insurance Portability and Accountability Act
- **PHI:** Protected Health Information
- **SOAP:** Subjective, Objective, Assessment, Plan (clinical note format)
- **JWT:** JSON Web Token (authentication)
- **VTT:** WebVTT (transcript format)
- **GCP:** Google Cloud Platform

### Related Documentation

- [HIPAA Audit Logs](./HIPAA_AUDIT_LOGS.md)
- [API Schema](./API_SCHEMA.md)
- Frontend Architecture (embedded in this doc)
- Backend Models (in code comments)

### Maintenance

**Update this document when:**
- Adding new major features
- Changing architecture patterns
- Updating technology stack
- Adding new deployment environments
- Making HIPAA-related changes

**Review schedule:**
- Monthly during active development
- Quarterly after initial release
- Before major feature releases
- Before compliance audits

---

**Document Status:** Living document - Updated 2026-01-17
**Maintainer:** Development team
**Last reviewed by:** Kurt Niemi

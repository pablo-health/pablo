# CLAUDE.md — backend/

Backend rules that apply to every file under `backend/`. The root
[`CLAUDE.md`](../CLAUDE.md) still applies.

## Backend Conventions

- **Python 3.13+** with Poetry — always use `poetry run`
- **Type annotations** everywhere: `str | None` not `Optional[str]`
- **Pydantic models** for API request/response validation
- **FastAPI `Depends()`** for dependency injection

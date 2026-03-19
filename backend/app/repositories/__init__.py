"""
Repository pattern for data access.

Enables future database migration without rewriting business logic.
"""

from ..database import get_firestore_client
from .allowlist import (
    AllowlistRepository,
    FirestoreAllowlistRepository,
    InMemoryAllowlistRepository,
)
from .appointment import FirestoreAppointmentRepository
from .availability_rule import FirestoreAvailabilityRuleRepository
from .patient import (
    FirestorePatientRepository,
    InMemoryPatientRepository,
    PatientRepository,
)
from .session import (
    FirestoreTherapySessionRepository,
    InMemoryTherapySessionRepository,
    TherapySessionRepository,
)
from .user import (
    FirestoreUserRepository,
    InMemoryUserRepository,
    UserRepository,
)


def get_user_repository() -> UserRepository:
    """Get user repository instance."""
    db = get_firestore_client()
    return FirestoreUserRepository(db)


def get_allowlist_repository() -> AllowlistRepository:
    """Get allowlist repository instance."""
    db = get_firestore_client()
    return FirestoreAllowlistRepository(db)


__all__ = [
    "AllowlistRepository",
    "FirestoreAllowlistRepository",
    "FirestoreAppointmentRepository",
    "FirestoreAvailabilityRuleRepository",
    "FirestorePatientRepository",
    "FirestoreTherapySessionRepository",
    "FirestoreUserRepository",
    "InMemoryAllowlistRepository",
    "InMemoryPatientRepository",
    "InMemoryTherapySessionRepository",
    "InMemoryUserRepository",
    "PatientRepository",
    "TherapySessionRepository",
    "UserRepository",
    "get_allowlist_repository",
    "get_user_repository",
]

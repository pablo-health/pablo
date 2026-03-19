"""
Scheduling engine — database-independent core logic.

Contains models, repository interfaces, and services for appointment
scheduling, availability computation, and recurrence generation.

This module has ZERO dependencies on backend/app/ infrastructure,
enabling future extraction into a standalone package.
"""

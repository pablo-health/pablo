# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for platform audit models, repository, and service."""

from unittest.mock import MagicMock

from app.models.platform_audit import (
    PlatformAuditAction,
    PlatformAuditLogEntry,
    PlatformResourceType,
)
from app.repositories.platform_audit import InMemoryPlatformAuditRepository
from app.services.platform_audit_service import PlatformAuditService


class TestPlatformAuditLogEntry:
    def test_to_dict_drops_none_fields(self) -> None:
        entry = PlatformAuditLogEntry(
            actor_user_id="admin-1",
            action=PlatformAuditAction.TENANT_PROVISIONED.value,
            resource_type=PlatformResourceType.TENANT.value,
            resource_id="practice-abc",
            tenant_schema="practice_abc",
        )
        data = entry.to_dict()
        assert data["actor_user_id"] == "admin-1"
        assert data["tenant_schema"] == "practice_abc"
        assert "ip_address" not in data
        assert "user_agent" not in data
        assert "details" not in data

    def test_auto_fields_populate(self) -> None:
        entry = PlatformAuditLogEntry(actor_user_id="x", action="y")
        assert entry.id
        assert entry.timestamp.endswith("Z")
        assert entry.expires_at > entry.timestamp  # 7y default retention


class TestPlatformAuditService:
    def test_log_tenant_action_captures_request_context(self) -> None:
        repo = InMemoryPlatformAuditRepository()
        service = PlatformAuditService(repo)
        mock_request = MagicMock()
        mock_request.headers = {
            "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
            "User-Agent": "pablo-pentest/1",
        }

        entry = service.log_tenant_action(
            action=PlatformAuditAction.PENTEST_TENANT_PROVISIONED,
            actor_user_id="pentest-runner",
            tenant_schema="practice_pentest_a1b2c3",
            tenant_id="pentest-a1b2c3",
            request=mock_request,
        )

        assert entry.action == "pentest_tenant_provisioned"
        assert entry.resource_type == "tenant"
        assert entry.tenant_schema == "practice_pentest_a1b2c3"
        # X-Forwarded-For: use leftmost (the real client)
        assert entry.ip_address == "203.0.113.10"
        assert entry.user_agent == "pablo-pentest/1"

        recent = repo.recent()
        assert len(recent) == 1
        assert recent[0].id == entry.id

    def test_log_without_request_has_null_context(self) -> None:
        repo = InMemoryPlatformAuditRepository()
        service = PlatformAuditService(repo)
        entry = service.log_tenant_action(
            action=PlatformAuditAction.TENANT_PROVISIONED,
            actor_user_id="admin",
            tenant_schema="practice_x",
            tenant_id="practice-x",
            request=None,
        )
        assert entry.ip_address is None
        assert entry.user_agent is None

    def test_repo_failure_bubbles(self) -> None:
        # Silent audit misses are a HIPAA gap; persist failures must surface.
        repo = MagicMock()
        repo.append.side_effect = RuntimeError("db down")
        service = PlatformAuditService(repo)
        try:
            service.log_tenant_action(
                action=PlatformAuditAction.TENANT_PROVISIONED,
                actor_user_id="a",
                tenant_schema="s",
                tenant_id="t",
            )
        except RuntimeError as exc:
            assert "db down" in str(exc)
        else:
            msg = "expected RuntimeError to propagate"
            raise AssertionError(msg)


class TestInMemoryRepo:
    def test_recent_returns_newest_first(self) -> None:
        repo = InMemoryPlatformAuditRepository()
        for i in range(5):
            repo.append(
                PlatformAuditLogEntry(
                    actor_user_id=f"u{i}",
                    action="tenant_provisioned",
                )
            )
        rows = repo.recent(limit=3)
        assert [r.actor_user_id for r in rows] == ["u4", "u3", "u2"]

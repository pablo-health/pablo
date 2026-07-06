# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-owned encrypted store (the patient-support / PHR case).

Retention here is the *patient's*, not a practice's: the record is
encrypted at rest with a key the patient controls (see ``vault``), expires
after a TTL, and can be removed in one call (``purge``). This is the
FTC-Health-Breach-Notification-Rule / PHR posture, deliberately separate
from the practice tenant DB — patient-owned data must not live in a
clinician's schema. The filesystem backend here mirrors the contract a
production per-patient object-storage backend would implement.
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from integrations.epic.ingest import ImportedRecord, ImportResult
from integrations.epic.vault import KeyProvider, Vault

_SOURCE = "epic"
_RECORD_FILE = "record.enc"
_MANIFEST_FILE = "manifest.json"
_DEFAULT_TTL = timedelta(days=30)
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")

JsonDict = dict[str, object]


@dataclass(frozen=True)
class StoredEntry:
    """A persisted vault entry's non-secret manifest plus its plaintext."""

    manifest: JsonDict
    payload: bytes


class PatientOwnedStore:
    """Encrypted-at-rest, TTL'd, patient-purgeable store on the filesystem.

    Each entry is a directory holding the Fernet ciphertext (``record.enc``)
    and a non-secret ``manifest.json`` (timestamps, TTL, provenance, counts).
    The key never touches disk.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(
        self,
        vault_id: str,
        plaintext: bytes,
        vault: Vault,
        *,
        ttl: timedelta | None,
        metadata: JsonDict,
    ) -> JsonDict:
        directory = self._dir(vault_id)
        directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        expires = now + ttl if ttl is not None else None
        manifest: JsonDict = {
            "vault_id": _safe_id(vault_id),
            "imported_at": now.isoformat(),
            "expires_at": expires.isoformat() if expires else None,
            **metadata,
        }
        (directory / _RECORD_FILE).write_bytes(vault.encrypt(plaintext))
        (directory / _MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def manifest(self, vault_id: str) -> JsonDict | None:
        path = self._dir(vault_id) / _MANIFEST_FILE
        if not path.exists():
            return None
        loaded: JsonDict = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def is_expired(self, vault_id: str) -> bool:
        manifest = self.manifest(vault_id)
        if manifest is None:
            return False
        expires = manifest.get("expires_at")
        return isinstance(expires, str) and datetime.fromisoformat(expires) <= datetime.now(UTC)

    def read(self, vault_id: str, vault: Vault) -> bytes | None:
        """Decrypt the entry, or ``None`` if it is missing or has expired."""
        path = self._dir(vault_id) / _RECORD_FILE
        if not path.exists() or self.is_expired(vault_id):
            return None
        return vault.decrypt(path.read_bytes())

    def purge(self, vault_id: str) -> bool:
        """Delete the entry entirely (the patient's one-tap delete)."""
        directory = self._dir(vault_id)
        if not directory.exists():
            return False
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
        return True

    def _dir(self, vault_id: str) -> Path:
        directory = (self._root / _safe_id(vault_id)).resolve()
        # Defence in depth: the sanitised id must resolve to a direct child of
        # the store root — never the root itself or anything outside it.
        if directory.parent != self._root.resolve():
            raise ValueError(f"unsafe vault id resolved outside the store: {vault_id!r}")
        return directory


class PatientOwnedSink:
    """Land an imported record into the patient's own encrypted store.

    The :class:`~integrations.epic.vault.KeyProvider` decides key custody:
    ``LocalKeyProvider`` for a patient-held (zero-knowledge) key, or
    ``CmekKeyProvider`` for a KMS-wrapped key Pablo can use server-side.
    Whatever key material the provider issues is persisted in the manifest.
    """

    def __init__(
        self,
        store: PatientOwnedStore,
        key_provider: KeyProvider,
        *,
        ttl: timedelta | None = _DEFAULT_TTL,
        metadata_extra: JsonDict | None = None,
    ) -> None:
        self._store = store
        self._key_provider = key_provider
        self._ttl = ttl
        self._metadata_extra = metadata_extra or {}

    def write(self, record: ImportedRecord) -> ImportResult:
        vault_id = record.patient.source_id or uuid4().hex
        vault, key_material = self._key_provider.issue()
        self._store.write(
            vault_id,
            record_payload(record),
            vault,
            ttl=self._ttl,
            metadata={
                "source": _SOURCE,
                "medications": len(record.medications),
                "conditions": len(record.conditions),
                "sensitive_skipped": record.sensitive_skipped,
                "key": key_material,
                **self._metadata_extra,
            },
        )
        return ImportResult(
            patient_id=vault_id,
            medications_created=len(record.medications),
            conditions_recorded=len(record.conditions),
            sensitive_skipped=record.sensitive_skipped,
        )


def load_payload(
    store: PatientOwnedStore, key_provider: KeyProvider, vault_id: str
) -> bytes | None:
    """Read a stored record back, rebuilding the key from the manifest."""
    manifest = store.manifest(vault_id)
    if manifest is None:
        return None
    key_material = manifest.get("key", {})
    vault = key_provider.restore(key_material if isinstance(key_material, dict) else {})
    return store.read(vault_id, vault)


def record_payload(record: ImportedRecord) -> bytes:
    """Serialize an imported record to the bytes stored under encryption."""
    return json.dumps(
        {
            "patient": asdict(record.patient),
            "medications": [asdict(m) for m in record.medications],
            "conditions": [asdict(c) for c in record.conditions],
            "sensitive_skipped": record.sensitive_skipped,
        }
    ).encode("utf-8")


def _safe_id(vault_id: str) -> str:
    cleaned = _UNSAFE_ID.sub("_", vault_id)
    # Every char left by _UNSAFE_ID is filesystem-legal, but "." and ".." are
    # still path-significant (parent/current dir). A vault id that is all dots
    # would escape or alias the store root, so fall back to a random id.
    if cleaned.strip(".") == "":
        return uuid4().hex
    return cleaned

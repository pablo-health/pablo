# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the patient-owned encrypted PHR store."""

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from integrations.epic.cmek import CmekKeyProvider
from integrations.epic.ingest import ImportedRecord
from integrations.epic.mappers import MappedCondition, MappedMedication, MappedPatient
from integrations.epic.patient_store import PatientOwnedSink, PatientOwnedStore, load_payload
from integrations.epic.vault import LocalKeyProvider, Vault, VaultError, derive_key, generate_key


def _record() -> ImportedRecord:
    return ImportedRecord(
        patient=MappedPatient("pat-9", "Ada", "Byron", "1815-12-10", None, None, "female", "MRN-9"),
        medications=(MappedMedication("m1", "Tamoxifen", "20 mg daily", "active", "2026-02-01"),),
        conditions=(MappedCondition("c1", "Breast cancer", "C50.9", "2026-01-15"),),
    )


def test_vault_round_trip_with_generated_key() -> None:
    vault = Vault(generate_key())
    token = vault.encrypt(b"secret labs")
    assert token != b"secret labs"
    assert vault.decrypt(token) == b"secret labs"


def test_vault_wrong_key_fails_loudly() -> None:
    token = Vault(generate_key()).encrypt(b"x")
    with pytest.raises(VaultError):
        Vault(generate_key()).decrypt(token)


def test_derive_key_is_deterministic_per_passphrase_and_salt() -> None:
    salt = b"0123456789abcdef"
    first = derive_key("correct horse", salt)
    assert derive_key("correct horse", salt) == first
    assert derive_key("other passphrase", salt) != first
    # The derived key actually decrypts data encrypted under it.
    assert Vault(first).decrypt(Vault(first).encrypt(b"labs")) == b"labs"


def test_sink_encrypts_at_rest_and_reads_back(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    provider = LocalKeyProvider(generate_key())

    result = PatientOwnedSink(store, provider).write(_record())
    assert result.patient_id == "pat-9"
    assert result.medications_created == 1

    # On-disk bytes are ciphertext — the plaintext drug name must not appear.
    on_disk = (tmp_path / "pat-9" / "record.enc").read_bytes()
    assert b"Tamoxifen" not in on_disk

    payload = json.loads(load_payload(store, provider, "pat-9") or b"{}")
    assert payload["medications"][0]["drug_name"] == "Tamoxifen"
    assert payload["patient"]["last_name"] == "Byron"


def test_local_provider_persists_no_key_material(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    PatientOwnedSink(store, LocalKeyProvider(generate_key())).write(_record())
    manifest = store.manifest("pat-9")
    assert manifest is not None
    assert manifest["key"] == {}  # zero-knowledge: nothing secret on disk


def test_manifest_records_provenance_and_counts(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    sink = PatientOwnedSink(
        store, LocalKeyProvider(generate_key()), metadata_extra={"consent_ref": "consent-123"}
    )
    sink.write(_record())

    manifest = store.manifest("pat-9")
    assert manifest is not None
    assert manifest["source"] == "epic"
    assert manifest["medications"] == 1
    assert manifest["consent_ref"] == "consent-123"
    assert manifest["imported_at"]


def test_ttl_expiry_hides_entry(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    provider = LocalKeyProvider(generate_key())
    # already expired
    PatientOwnedSink(store, provider, ttl=timedelta(seconds=-1)).write(_record())

    assert store.is_expired("pat-9")
    assert load_payload(store, provider, "pat-9") is None


def test_purge_removes_everything_and_is_idempotent(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    provider = LocalKeyProvider(generate_key())
    PatientOwnedSink(store, provider).write(_record())

    assert store.purge("pat-9") is True
    assert load_payload(store, provider, "pat-9") is None
    assert store.manifest("pat-9") is None
    assert store.purge("pat-9") is False  # nothing left to remove


class _FakeKms:
    """Reversible stand-in for Cloud KMS encrypt/decrypt (envelope wrap)."""

    def __init__(self) -> None:
        self.wraps = 0

    def encrypt(self, *, name: str, plaintext: bytes) -> SimpleNamespace:
        self.wraps += 1
        return SimpleNamespace(ciphertext=b"wrapped|" + name.encode() + b"|" + plaintext)

    def decrypt(self, *, name: str, ciphertext: bytes) -> SimpleNamespace:
        return SimpleNamespace(plaintext=ciphertext.split(b"|", 2)[2])


def test_cmek_envelope_wraps_dek_and_round_trips(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    kms = _FakeKms()
    key_name = "projects/p/locations/l/keyRings/r/cryptoKeys/patient-9"
    provider = CmekKeyProvider(key_name, client=kms)

    PatientOwnedSink(store, provider).write(_record())
    assert kms.wraps == 1  # one KMS wrap per record, not per byte

    manifest = store.manifest("pat-9")
    assert manifest is not None
    key_material = manifest["key"]
    assert isinstance(key_material, dict)
    assert key_material["scheme"] == "cmek-envelope"
    assert key_material["kms_key"] == key_name
    assert "wrapped_dek" in key_material  # the DEK on disk is KMS-wrapped, not raw

    payload = json.loads(load_payload(store, provider, "pat-9") or b"{}")
    assert payload["patient"]["last_name"] == "Byron"

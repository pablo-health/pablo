# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the patient-owned encrypted PHR store."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from integrations.epic.ingest import ImportedRecord
from integrations.epic.mappers import MappedCondition, MappedMedication, MappedPatient
from integrations.epic.patient_store import PatientOwnedSink, PatientOwnedStore
from integrations.epic.vault import Vault, VaultError, derive_key, generate_key


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
    vault = Vault(generate_key())

    result = PatientOwnedSink(store, vault).write(_record())
    assert result.patient_id == "pat-9"
    assert result.medications_created == 1

    # On-disk bytes are ciphertext — the plaintext drug name must not appear.
    on_disk = (tmp_path / "pat-9" / "record.enc").read_bytes()
    assert b"Tamoxifen" not in on_disk

    payload = json.loads(store.read("pat-9", vault) or b"{}")
    assert payload["medications"][0]["drug_name"] == "Tamoxifen"
    assert payload["patient"]["last_name"] == "Byron"


def test_manifest_records_provenance_and_counts(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    sink = PatientOwnedSink(
        store, Vault(generate_key()), metadata_extra={"consent_ref": "consent-123"}
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
    vault = Vault(generate_key())
    PatientOwnedSink(store, vault, ttl=timedelta(seconds=-1)).write(_record())  # already expired

    assert store.is_expired("pat-9")
    assert store.read("pat-9", vault) is None


def test_purge_removes_everything_and_is_idempotent(tmp_path: Path) -> None:
    store = PatientOwnedStore(tmp_path)
    vault = Vault(generate_key())
    PatientOwnedSink(store, vault).write(_record())

    assert store.purge("pat-9") is True
    assert store.read("pat-9", vault) is None
    assert store.manifest("pat-9") is None
    assert store.purge("pat-9") is False  # nothing left to remove

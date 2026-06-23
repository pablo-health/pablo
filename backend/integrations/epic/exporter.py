# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pull a patient's FHIR resources and write them to disk as JSON."""

import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from integrations.epic.fhir_client import FhirClient, JsonDict


@dataclass(frozen=True)
class ResourceQuery:
    """A patient-scoped FHIR search to run and the file label to save it under."""

    resource_type: str
    label: str
    params: dict[str, str]


# Patient-scoped searches. The patient reference is injected at runtime,
# so each query only carries its extra filters. Observation is split by
# category because Epic requires a category on Observation searches.
PATIENT_RESOURCE_QUERIES: tuple[ResourceQuery, ...] = (
    ResourceQuery("AllergyIntolerance", "AllergyIntolerance", {}),
    ResourceQuery("Condition", "Condition", {}),
    ResourceQuery("MedicationRequest", "MedicationRequest", {}),
    ResourceQuery("Observation", "Observation_laboratory", {"category": "laboratory"}),
    ResourceQuery("Observation", "Observation_vital-signs", {"category": "vital-signs"}),
    ResourceQuery("Immunization", "Immunization", {}),
    ResourceQuery("Procedure", "Procedure", {}),
    ResourceQuery("DiagnosticReport", "DiagnosticReport", {}),
    ResourceQuery("DocumentReference", "DocumentReference", {}),
    ResourceQuery("Encounter", "Encounter", {}),
)


@dataclass(frozen=True)
class ExportSummary:
    """Where an export landed and how many resources each query yielded."""

    output_dir: Path
    counts: dict[str, int]


def export_patient_data(
    client: FhirClient,
    patient_id: str,
    output_dir: Path,
    queries: tuple[ResourceQuery, ...] = PATIENT_RESOURCE_QUERIES,
) -> ExportSummary:
    """Pull the patient record + scoped searches into a timestamped run dir."""
    run_dir = output_dir / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    patient = client.read("Patient", patient_id)
    _write_json(run_dir / "Patient.json", patient)
    counts: dict[str, int] = {"Patient": 1}

    doc_bundle: JsonDict | None = None
    for query in queries:
        try:
            bundle = client.search(query.resource_type, {"patient": patient_id, **query.params})
        except httpx.HTTPStatusError as exc:
            # A resource type whose scope wasn't granted returns 403/401 —
            # skip it and keep going rather than failing the whole export.
            status = exc.response.status_code
            counts[query.label] = -1
            _write_json(
                run_dir / f"{query.label}.error.json",
                {"label": query.label, "status_code": status, "error": str(exc)},
            )
            print(f"  skipped {query.label}: HTTP {status} (scope not granted?)")
            continue
        _write_json(run_dir / f"{query.label}.json", bundle)
        # Count only real matches — Epic returns an OperationOutcome entry
        # (e.g. "no results" / sub-resource not authorized) that would
        # otherwise inflate an empty result to a misleading "1".
        counts[query.label] = sum(
            1
            for entry in bundle.get("entry", [])
            if entry.get("resource", {}).get("resourceType") == query.resource_type
        )
        if query.resource_type == "DocumentReference":
            doc_bundle = bundle

    if doc_bundle is not None:
        _fetch_document_bodies(client, doc_bundle, run_dir, counts)

    _write_json(
        run_dir / "_export_metadata.json",
        {
            "patient_id": patient_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "counts": counts,
        },
    )
    return ExportSummary(output_dir=run_dir, counts=counts)


def _fetch_document_bodies(
    client: FhirClient, doc_bundle: JsonDict, run_dir: Path, counts: dict[str, int]
) -> None:
    """Resolve each DocumentReference's Binary attachment into readable files.

    Document *content* is a separately-gated scope from the DocumentReference
    index, so individual fetches may 403 even when the index succeeded — those
    are skipped, not fatal.
    """
    docs_dir = run_dir / "documents"
    saved = 0
    failed = 0
    resources = [
        e["resource"]
        for e in doc_bundle.get("entry", [])
        if e.get("resource", {}).get("resourceType") == "DocumentReference"
    ]
    for i, res in enumerate(resources):
        label = ((res.get("type") or {}).get("text") or "Document").replace("/", "-")
        date = (res.get("date") or "")[:10]
        for content in res.get("content", []):
            url = (content.get("attachment") or {}).get("url")
            if not url:
                continue
            try:
                ctype, data = client.get_binary(url)
            except httpx.HTTPStatusError:
                failed += 1
                continue
            docs_dir.mkdir(exist_ok=True)
            ext = "html" if "html" in ctype else ("rtf" if "rtf" in ctype else "txt")
            stem = f"{date}_{label}_{i}"
            (docs_dir / f"{stem}.{ext}").write_bytes(data)
            if "html" in ctype:
                (docs_dir / f"{stem}.txt").write_text(_html_to_text(data), encoding="utf-8")
            saved += 1
    counts["DocumentBodies"] = saved
    if failed:
        counts["DocumentBodies_blocked"] = -failed
    print(f"  document bodies: {saved} saved, {failed} blocked (403)")


def _html_to_text(raw: bytes) -> str:
    """Crude HTML→text for readable note bodies."""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)  # decode named + numeric entities (&#183;, &nbsp;, …)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _write_json(path: Path, data: JsonDict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

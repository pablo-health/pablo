/**
 * Patient document upload — end-to-end.
 *
 * Uses the tiered fixture pattern (auth.ts):
 *   - onboardedUser:  worker-scoped; one user, real UI onboarding run once
 *   - signedInPage:   per-test page that starts already-signed-in via the
 *                     onboarded user's saved storageState
 *   - scenarios.givePatient: API-level "given a patient" helper
 *
 * What this proves:
 *   1. A signed-in user can upload a document via the signed-URL flow
 *      (backend/app/routes/patient_documents.py +
 *      frontend/src/lib/api/patientDocuments.ts).
 *   2. The uploaded bytes are byte-identical to the source file
 *      (layer 1: download via app + SHA-256 compare).
 *   3. (optional) The bucket object metadata is correct — size, MD5,
 *      content-type, and the object isn't public (layer 2: direct GCS
 *      inspection). Layer 2 runs only when PATIENT_DOCS_BUCKET is set
 *      and the runner has storage.objectViewer on that bucket.
 *   4. Delete soft-removes the document from the user's list.
 *
 * What this does NOT prove (out of scope, separate specs):
 *   - The onboarding wizard itself.
 *   - Cross-user IDOR (pytest covers).
 *   - OCR / text extraction quality.
 *
 * Single-tenant note: with ENABLE_MULTI_TENANCY=false the backend stores
 * objects under a fixed `default/<category>/<uuid>` prefix (see
 * backend/app/services/patient_documents_service.py:_object_name), so
 * layer 2 looks under the `default/` prefix.
 */
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "../fixtures/auth";
import { waitForAppReady } from "../flows/onboarding";
import { getRecentlyUploadedObject, md5Hex, sha256Hex } from "../fixtures/gcs";
import { scenarios } from "../fixtures/scenarios";

const FIXTURE_PDF_PATH = resolve(__dirname, "../fixtures/files/tiny.pdf");

// Layer 2 (direct GCS inspection) is opt-in: the OSS backend has no
// default patient-documents bucket name, and the least-privilege e2e
// runner SA isn't granted storage.objectViewer. Set PATIENT_DOCS_BUCKET
// (and grant the role) to enable it; otherwise layer 1 alone runs.
const PATIENT_DOCS_BUCKET = process.env.PATIENT_DOCS_BUCKET;

type DownloadUrlResponse = { url: string };

test("uploads a patient document and verifies bytes via download (and GCS when configured)", async ({
  onboardedUser,
  signedInPage,
}) => {
  const page = signedInPage;

  // 1. Given a patient (created via API — fast, deterministic).
  const patient = await scenarios.givePatient(onboardedUser);

  // 2. Navigate to that patient's detail page in the UI.
  await page.goto(`/dashboard/patients/${patient.id}`);
  await waitForAppReady(page);

  // Documents live in the Chart tabs. Radix unmounts inactive tab
  // panels, so the file input below isn't in the DOM until the
  // Documents tab is selected — the visible "Documents" text is the
  // tab trigger label, not the panel. Click into the tab first.
  await page.getByRole("tab", { name: /Documents/ }).click();
  await expect(
    page.locator('[data-testid="patient-document-file-input"]'),
  ).toBeAttached({ timeout: 10_000 });

  // 3. Upload the fixture PDF via the file input.
  const fixtureBuf = await readFile(FIXTURE_PDF_PATH);
  const expectedSize = fixtureBuf.length;
  const expectedSha = sha256Hex(fixtureBuf);
  const expectedMd5 = md5Hex(fixtureBuf);
  const sinceMs = Date.now() - 1000;

  await page
    .locator('[data-testid="patient-document-file-input"]')
    .setInputFiles(FIXTURE_PDF_PATH);

  // Wait for finalize to complete by watching the network. The SPA's
  // React Query invalidation should refresh the list, but sometimes
  // the cache hasn't repainted by the time we look — fall back to a
  // page.reload() if the document doesn't surface on its own.
  await page.waitForResponse(
    (r) => r.url().includes("/api/documents/") && r.url().includes("/finalize") && r.ok(),
    { timeout: 30_000 },
  );
  // Give React Query a beat to invalidate and refetch.
  await page.waitForTimeout(500);
  const docTile = page.getByText("tiny.pdf");
  if (!(await docTile.isVisible().catch(() => false))) {
    await page.reload();
    await expect(page.getByText(/^Documents$/)).toBeVisible({ timeout: 10_000 });
  }
  await expect(docTile).toBeVisible({ timeout: 30_000 });

  // 4. LAYER 1 — round-trip via the app's signed-URL download path.
  const idToken = await onboardedUser.getIdToken();
  const apiUrl = onboardedUser.apiUrl;

  const listResp = await page.request.get(
    `${apiUrl}/api/patients/${patient.id}/documents`,
    { headers: { Authorization: `Bearer ${idToken}` } },
  );
  expect(listResp.ok(), "list documents endpoint").toBe(true);
  const list = (await listResp.json()) as {
    data: Array<{ id: string }>;
    total: number;
  };
  const docId = list.data?.[0]?.id;
  expect(docId, "document id from list endpoint").toBeTruthy();

  // Backend GET /api/documents/{id}/file returns JSON with a short-lived
  // signed GCS GET URL (a 302 here would 401 before the redirect since a
  // raw navigation can't carry our Authorization header — see PABLO-47h).
  // Fetch the JSON through the authenticated client, then fetch the
  // signed URL directly (no auth header — the signature authorizes it).
  const urlResp = await page.request.get(`${apiUrl}/api/documents/${docId}/file`, {
    headers: { Authorization: `Bearer ${idToken}` },
  });
  expect(urlResp.ok(), "file endpoint returns a signed URL").toBe(true);
  const { url: signedUrl } = (await urlResp.json()) as DownloadUrlResponse;
  expect(signedUrl, "signed download URL present").toBeTruthy();

  const dl = await page.request.get(signedUrl);
  expect(dl.ok(), "signed URL fetch returns 200").toBe(true);
  const downloadedBytes = Buffer.from(await dl.body());
  expect(downloadedBytes.length, "downloaded size").toBe(expectedSize);
  expect(sha256Hex(downloadedBytes), "round-trip SHA-256 matches").toBe(
    expectedSha,
  );
  expect(dl.headers()["content-type"]).toContain("application/pdf");

  // 5. LAYER 2 — direct GCS object inspection (opt-in).
  if (PATIENT_DOCS_BUCKET) {
    const obj = await getRecentlyUploadedObject({
      bucket: PATIENT_DOCS_BUCKET,
      prefix: "default/",
      sinceMs,
    });
    expect(obj.sizeBytes, "GCS object size").toBe(expectedSize);
    expect(obj.md5Hex, "GCS object MD5").toBe(expectedMd5);
    expect(obj.contentType, "GCS object content-type").toBe("application/pdf");
    expect(obj.isPublic, "bucket is not public").toBe(false);
    expect(
      obj.name.startsWith("default/"),
      "single-tenant default prefix",
    ).toBe(true);
  }

  // 6. Delete and verify removal. The PatientDocuments component uses a
  // native window.confirm() (frontend/src/components/patients/
  // PatientDocuments.tsx), not a custom modal — so we register a dialog
  // handler that auto-accepts, then click Delete.
  page.once("dialog", (dialog) => {
    void dialog.accept();
  });
  await page.getByRole("button", { name: /^Delete$/i }).first().click();
  await expect(page.getByText("tiny.pdf")).toBeHidden({ timeout: 10_000 });
});

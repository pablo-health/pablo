/**
 * Uploads playwright-report/ and test-results/ to GCS.
 *
 * Authenticates via ADC inside the Cloud Run Job (the job's SA must have
 * roles/storage.objectAdmin on the destination bucket).
 *
 * Env vars:
 *   E2E_ARTIFACTS_BUCKET   bucket name (no gs:// prefix), e.g. "pablohealth-dev-e2e-artifacts"
 *   RUN_ID                 destination prefix
 *
 * Exits 0 on success or partial success; never aborts the test outcome.
 */
import { Storage } from "@google-cloud/storage";
import { readdir, stat } from "node:fs/promises";
import { join, relative } from "node:path";

const BUCKET = process.env.E2E_ARTIFACTS_BUCKET;
const RUN_ID = process.env.RUN_ID;
const ROOTS = ["playwright-report", "test-results"];

async function* walk(dir: string): AsyncGenerator<string> {
  let entries: import("node:fs").Dirent[];
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(path);
    } else if (entry.isFile()) {
      yield path;
    }
  }
}

async function main() {
  if (!BUCKET || !RUN_ID) {
    console.log("[upload] E2E_ARTIFACTS_BUCKET or RUN_ID unset; skipping");
    return;
  }
  const storage = new Storage();
  const bucket = storage.bucket(BUCKET);
  let uploaded = 0;
  let failed = 0;
  for (const root of ROOTS) {
    try {
      await stat(root);
    } catch {
      continue;
    }
    for await (const file of walk(root)) {
      const destination = `${RUN_ID}/${relative(".", file)}`;
      try {
        await bucket.upload(file, { destination });
        uploaded += 1;
      } catch (err) {
        failed += 1;
        console.error(`[upload] failed: ${file} -> ${destination}`, err);
      }
    }
  }
  console.log(
    `[upload] complete: ${uploaded} uploaded, ${failed} failed to gs://${BUCKET}/${RUN_ID}/`,
  );
}

main().catch((err) => {
  console.error("[upload] fatal:", err);
  // never mask the test exit code
  process.exit(0);
});

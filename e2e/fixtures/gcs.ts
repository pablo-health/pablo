/**
 * GCS-side verification helpers for the patient document upload spec
 * (layer 2).
 *
 * Layer 1 (round-trip through the app's download endpoint, hash compare)
 * is in the spec itself. Layer 2 (this file) inspects the actual bucket
 * object to catch things the round-trip can't: wrong content-type
 * metadata, accidentally-public ACL, landed in the wrong prefix.
 *
 * Requires the test runner to have `roles/storage.objectViewer` on the
 * patient-documents bucket. This is an OPTIONAL layer — the spec skips
 * it when PATIENT_DOCS_BUCKET is unset, so the suite still runs with the
 * least-privilege runtime SA.
 *
 * NEVER point this at a bucket holding real PHI.
 */
import { Storage, type File } from "@google-cloud/storage";
import { createHash } from "node:crypto";

let cachedStorage: Storage | undefined;
function getStorage(): Storage {
  cachedStorage ??= new Storage();
  return cachedStorage;
}

export type BucketObjectInfo = {
  name: string;
  sizeBytes: number;
  contentType: string;
  md5Hex: string;
  crc32cBase64: string;
  isPublic: boolean;
  createdAt: Date;
};

/**
 * List objects under `gs://<bucket>/<prefix>/` and return the
 * single most-recent object created at or after `sinceMs`. Throws if
 * 0 or 2+ matches — the caller is expected to scope the prefix tightly
 * and use a narrow time window.
 */
export async function getRecentlyUploadedObject(args: {
  bucket: string;
  prefix: string;
  sinceMs: number;
}): Promise<BucketObjectInfo> {
  const storage = getStorage();
  const [files] = await storage
    .bucket(args.bucket)
    .getFiles({ prefix: args.prefix });

  const recent = files.filter((f) => {
    const t = f.metadata.timeCreated;
    return typeof t === "string" && new Date(t).getTime() >= args.sinceMs;
  });

  if (recent.length === 0) {
    throw new Error(
      `No objects under gs://${args.bucket}/${args.prefix} since ${new Date(
        args.sinceMs,
      ).toISOString()}`,
    );
  }
  if (recent.length > 1) {
    const names = recent.map((f) => f.name).join(", ");
    throw new Error(
      `Expected exactly 1 recent object, got ${recent.length}: ${names}`,
    );
  }
  return await describeFile(recent[0]);
}

async function describeFile(file: File): Promise<BucketObjectInfo> {
  await file.getMetadata();
  const md = file.metadata;
  const md5Base64 = String(md.md5Hash ?? "");
  const md5Hex = md5Base64
    ? Buffer.from(md5Base64, "base64").toString("hex")
    : "";
  // ACL check: UBLA buckets don't have per-object ACLs, but we can
  // still confirm allUsers / allAuthenticatedUsers isn't bound at the
  // bucket level. For UBLA we treat object as private if bucket IAM
  // doesn't grant public access — check via getIamPolicy.
  const isPublic = await isFilePublic(file);
  return {
    name: file.name,
    sizeBytes: Number(md.size ?? 0),
    contentType: String(md.contentType ?? ""),
    md5Hex,
    crc32cBase64: String(md.crc32c ?? ""),
    isPublic,
    createdAt: new Date(String(md.timeCreated ?? Date.now())),
  };
}

async function isFilePublic(file: File): Promise<boolean> {
  const [policy] = await file.bucket.iam.getPolicy({ requestedPolicyVersion: 3 });
  const publicMembers = new Set(["allUsers", "allAuthenticatedUsers"]);
  for (const binding of policy.bindings ?? []) {
    for (const m of binding.members ?? []) {
      if (publicMembers.has(m)) return true;
    }
  }
  return false;
}

/** Compute hex SHA-256 of a Node Buffer. */
export function sha256Hex(buf: Buffer): string {
  return createHash("sha256").update(buf).digest("hex");
}

/** Compute hex MD5 of a Node Buffer (matches GCS md5Hash decoded form). */
export function md5Hex(buf: Buffer): string {
  return createHash("md5").update(buf).digest("hex");
}

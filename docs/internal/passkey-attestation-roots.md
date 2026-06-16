# Provisioning the WebAuthn attestation trust store

The passkey verification build (PABLO-f00) can verify an authenticator's
*provenance* — proving a credential was minted by a genuine Apple / Yubico /
Google / Microsoft authenticator rather than just claiming to be one. It does
this by validating the attestation certificate chain against a curated set of
vendor root CAs.

The verification **engine ships ready**; the **trust store ships empty** by
design. Root-CA bytes are a security anchor: they must be downloaded from the
vendor and **fingerprint-verified by a human**, never embedded by tooling.
Until the store is provisioned, `attestation_verified` is always `false`
(informational only) and nothing is rejected — credentials still enrol.

## Layout

Point `WEBAUTHN_ATTESTATION_ROOTS_DIR` at a directory containing one PEM file
per attestation format you trust, named `<fmt>.pem`:

| File            | Authenticators it covers                          | Source |
|-----------------|---------------------------------------------------|--------|
| `apple.pem`     | Apple platform passkeys (Touch ID / Face ID)      | Apple WebAuthn Root CA (apple.com/certificateauthority) |
| `packed.pem`    | Most FIDO2 keys incl. Yubico (packed attestation) | Vendor root, e.g. Yubico FIDO Root CA via FIDO MDS |
| `fido-u2f.pem`  | Legacy U2F security keys                           | Vendor root |
| `tpm.pem`       | Windows Hello (TPM attestation)                   | Microsoft TPM root CAs |

Each file may concatenate several PEM roots for that format. Formats with no
file are simply not anchored — a credential of that format records
`attestation_verified=false`.

## Provisioning steps (per environment)

1. Download each root CA from the vendor's official certificate-authority page.
2. **Verify the SHA-256 fingerprint** against the vendor's published value
   before trusting the bytes. Do not skip this — a wrong root silently breaks
   the trust guarantee.
3. Place the PEMs in a directory the Cloud Run service can read (a mounted
   secret volume or a baked-in image path).
4. Set `WEBAUTHN_ATTESTATION_ROOTS_DIR` to that path on **dev first**, enrol a
   known hardware key, and confirm the credential lands with
   `attestation_verified=true` (see the `passkey_enrolled ... attested=True`
   log line). Then roll to prod.

## Strict mode

`WEBAUTHN_ATTESTATION_REQUIRE_TRUSTED_ROOT=true` rejects enrolment when an
authenticator presents an attestation of a format we *do* have roots for but
its chain fails to validate. Leave it `false` (the default) until the store is
proven in dev — otherwise a missing/wrong root blocks legitimate enrolments.

## Relationship to admin hardware-key enforcement

`WEBAUTHN_ADMIN_REQUIRE_HARDWARE_KEY` keys off the device-bound (BE) flag, which
needs **no** trust store — so admin enforcement is testable before roots are
provisioned. Provisioning roots additionally lights up the `att` signal in the
minted token (`pablo_passkey.att`) for future attested-only policies.

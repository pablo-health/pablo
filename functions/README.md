# Identity Platform blocking functions

`beforeCreate` / `beforeSignIn` (gen-2 Cloud Functions, Node 22). On every
sign-up and sign-in, Identity Platform calls these, and they in turn call the
backend (`/api/ext/auth/check-allowlist`, `/api/ext/auth/check-status`) to
decide whether the user may proceed.

## Deploying

```bash
cd functions && npm ci && npm run build && cd ..
cp functions/.env.example functions/.env.<project-id>   # set PABLO_BACKEND_URL
firebase deploy --only functions --project <project-id>
```

Notes that have bitten before:

- `PABLO_BACKEND_URL` must equal the backend's `BACKEND_BASE_URL` — the value
  is the OIDC audience on both ends of the function → backend call.
- `src/index.ts` configures **Direct VPC Egress** (`setGlobalOptions`) so the
  outbound call to the backend counts as internal traffic. This is required
  when the backend's Cloud Run ingress is `internal-and-cloud-load-balancing`;
  the network/subnet (`default`/`default`) must exist with Private Google
  Access enabled on the region's subnet. A plain `gcloud run services update`
  on the function does NOT apply these — deploy through the Firebase CLI so
  the source-of-truth options take effect.
- If the function's container image has been cleaned out of the
  `gcf-artifacts` Artifact Registry repo, config-only updates fail with
  "image not found" — a full source deploy (above) is the fix.
- The functions must already be registered as blocking triggers in the
  project's Identity Platform settings; deploying updates the code/config but
  does not (re)register triggers.

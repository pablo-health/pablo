# Identity Platform blocking functions (optional)

`beforeCreate` / `beforeSignIn` (gen-2 Cloud Functions, Node 22). On every
sign-up and sign-in, Identity Platform calls these, and they in turn call the
backend (`/api/ext/auth/check-allowlist`, `/api/ext/auth/check-status`) to
decide whether the user may proceed.

They are defense in depth. Sign-in does not depend on them:

| What the function checks | Where the backend enforces it regardless |
|---|---|
| `beforeCreate`: the email is on the allowlist | `_resolve_user` in `backend/app/auth/service.py` refuses a non-allowlisted account before it creates a Pablo user (`SIGNUP_NOT_ALLOWED`) |
| `beforeSignIn`: the account is not disabled | the same seam refuses a disabled account on every authenticated request (`USER_DISABLED`) |

Sign-in auditing does not depend on them either. The backend writes the
`signed_in` row to the practice audit log on the first authenticated request
of each sign-in (`backend/app/auth/sign_in_audit.py`), and
`/api/ext/auth/check-status` no longer writes it, so keeping the hook
registered does not produce a second row.

What keeping them buys: with `beforeCreate` registered, an address that is not
on the allowlist cannot create an account in the identity provider at all,
instead of creating one the backend then refuses; with `beforeSignIn`
registered, a disabled account is turned away before a token is issued,
instead of on its first request.

## Unregistering them

A deployment may drop the triggers. Identity Platform calls only the functions
registered in its config, so clearing the registration is enough; deleting the
deployed functions afterwards is optional cleanup.

```bash
curl -X PATCH   "https://identitytoolkit.googleapis.com/admin/v2/projects/<project-id>/config?updateMask=blockingFunctions"   -H "Authorization: Bearer $(gcloud auth print-access-token)"   -H "Content-Type: application/json"   -d '{"blockingFunctions": {"triggers": {}}}'
```

Or in the console: Identity Platform > Settings > Triggers, then clear the
`beforeCreate` and `beforeSignIn` functions. To confirm, sign in and check that
a `signed_in` entry appears in your own audit log in Settings.

The `/api/ext/auth/*` endpoints stay for deployments that keep the functions
registered. With the functions gone, nothing calls them, and
`BLOCKING_FUNCTION_SERVICE_ACCOUNT` may be left unset.

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

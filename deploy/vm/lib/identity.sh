# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
# shellcheck shell=bash disable=SC2034  # sets globals that install.sh reads
#
# Identity Platform for install.sh: Firebase on the project, a web app, email
# and Google sign-in, the NIST password policy and authenticator-app MFA.
# Sourced, not run. Every function is safe to repeat. Expects PROJECT_ID.

_api() {
    local method="$1" url="$2" body="${3:-}"
    local args=(-sS -X "$method" "$url"
        -H "Authorization: Bearer $(gcloud auth print-access-token)"
        -H "X-Goog-User-Project: ${PROJECT_ID}"
        -H "Content-Type: application/json")
    [ -n "$body" ] && args+=(-d "$body")
    curl "${args[@]}"
}

_status() {
    curl -s -o /dev/null -w "%{http_code}" "$1" \
        -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "X-Goog-User-Project: ${PROJECT_ID}"
}

# _json <python expression over d> — reads JSON on stdin.
_json() { python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"; }

readonly _FIREBASE="https://firebase.googleapis.com/v1beta1"
readonly _IDTK="https://identitytoolkit.googleapis.com/v2/projects"

_wait_for() {
    local url="$1" tries=24
    while [ "$(_status "$url")" != "200" ]; do
        tries=$((tries - 1))
        [ "$tries" -gt 0 ] || { echo "Timed out waiting for ${url}" >&2; return 1; }
        sleep 5
    done
}

ensure_firebase() {
    if [ "$(_status "${_FIREBASE}/projects/${PROJECT_ID}")" != "200" ]; then
        _api POST "${_FIREBASE}/projects/${PROJECT_ID}:addFirebase" >/dev/null
        _wait_for "${_FIREBASE}/projects/${PROJECT_ID}"
    fi
}

# Sets FIREBASE_APP_ID, FIREBASE_API_KEY, FIREBASE_AUTH_DOMAIN.
ensure_web_app() {
    FIREBASE_APP_ID=$(_api GET "${_FIREBASE}/projects/${PROJECT_ID}/webApps" \
        | _json "(d.get('apps') or [{}])[0].get('appId', '')")
    if [ -z "$FIREBASE_APP_ID" ]; then
        local operation tries=24
        operation=$(_api POST "${_FIREBASE}/projects/${PROJECT_ID}/webApps" '{"displayName": "Pablo"}' \
            | _json "d.get('name') or d.get('error', {}).get('message', 'unknown error')")
        [[ "$operation" == operations/* ]] || { echo "Creating the Firebase web app failed: ${operation}" >&2; return 1; }
        while [ -z "$FIREBASE_APP_ID" ]; do
            tries=$((tries - 1))
            [ "$tries" -gt 0 ] || { echo "Timed out creating the Firebase web app" >&2; return 1; }
            sleep 5
            FIREBASE_APP_ID=$(_api GET "${_FIREBASE}/${operation}" \
                | _json "d.get('response', {}).get('appId', '') if d.get('done') else ''")
        done
    fi
    local config
    config=$(_api GET "${_FIREBASE}/projects/${PROJECT_ID}/webApps/${FIREBASE_APP_ID}/config")
    FIREBASE_API_KEY=$(_json "d['apiKey']" <<<"$config")
    FIREBASE_AUTH_DOMAIN=$(_json "d['authDomain']" <<<"$config")
}

configure_identity_platform() {
    if [ "$(_status "${_IDTK}/${PROJECT_ID}/config")" != "200" ]; then
        _api POST "${_IDTK}/${PROJECT_ID}/identityPlatform:initializeAuth" '{}' >/dev/null
        _wait_for "${_IDTK}/${PROJECT_ID}/config"
    fi
    _api PATCH "${_IDTK}/${PROJECT_ID}/config?updateMask=signIn.email" \
        '{"signIn": {"email": {"enabled": true, "passwordRequired": true}}}' >/dev/null
    # NIST 800-63B: length, not composition rules.
    _api PATCH "${_IDTK}/${PROJECT_ID}/config?updateMask=passwordPolicyConfig" \
        '{"passwordPolicyConfig": {"passwordPolicyEnforcementState": "ENFORCE",
          "passwordPolicyVersions": [{"customStrengthOptions": {"minPasswordLength": 15, "maxPasswordLength": 128}}],
          "forceUpgradeOnSignin": true}}' >/dev/null
    # Authenticator apps only; no SMS.
    _api PATCH "${_IDTK}/${PROJECT_ID}/config?updateMask=mfa" \
        '{"mfa": {"state": "ENABLED", "providerConfigs": [{"state": "ENABLED", "totpProviderConfig": {"adjacentIntervals": 5}}]}}' >/dev/null
}

# register_google_idp <client id> <client secret>
register_google_idp() {
    local body
    body=$(python3 -c 'import json, sys; print(json.dumps({"enabled": True, "clientId": sys.argv[1], "clientSecret": sys.argv[2]}))' "$1" "$2")
    if [ "$(_status "${_IDTK}/${PROJECT_ID}/defaultSupportedIdpConfigs/google.com")" = "200" ]; then
        _api PATCH "${_IDTK}/${PROJECT_ID}/defaultSupportedIdpConfigs/google.com?updateMask=clientId,clientSecret,enabled" "$body" >/dev/null
    else
        _api POST "${_IDTK}/${PROJECT_ID}/defaultSupportedIdpConfigs?idpId=google.com" "$body" >/dev/null
    fi
}

# add_authorized_domain <hostname>
add_authorized_domain() {
    local domains
    domains=$(_api GET "${_IDTK}/${PROJECT_ID}/config" | python3 -c '
import sys, json
domains = json.load(sys.stdin).get("authorizedDomains", [])
if sys.argv[1] not in domains:
    domains.append(sys.argv[1])
print(json.dumps({"authorizedDomains": domains}))' "$1")
    _api PATCH "${_IDTK}/${PROJECT_ID}/config?updateMask=authorizedDomains" "$domains" >/dev/null
}

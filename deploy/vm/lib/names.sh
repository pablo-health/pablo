# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
# shellcheck shell=bash
#
# The optional hosted-subdomain service for install.sh: a name under
# NAMES_ZONE, kept by NAMES_ENDPOINT, whose DNS record points at the VM.
# Sourced, not run. This side only checks a name before anything is created;
# the VM claims and renews it itself, as only the VM can (bin/pablo-name).
# Expects NAMES_ENDPOINT and NAMES_ZONE.

# The service's rules, checked here first so a typo costs no round trip.
valid_hosted_name() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$ ]]; }

# check_hosted_name <name> — 0 when it can be claimed. Otherwise prints the
# reason and returns 1 when the name is refused, 2 when the service did not
# answer. Its reasons are sentences meant to be shown as they are.
check_hosted_name() {
    local name="$1" body code answer
    if ! valid_hosted_name "$name"; then
        echo "Use 3 to 40 lowercase letters, numbers and hyphens, not starting or ending with a hyphen."
        return 1
    fi
    body="$(mktemp)"
    code="$(curl -sS --max-time 15 -o "$body" -w '%{http_code}' "${NAMES_ENDPOINT}/v1/names/${name}" 2>/dev/null)" || code=000
    answer="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except ValueError:
    d = {}
if sys.argv[2] == "200" and d.get("available") is True:
    print("available")
elif sys.argv[2] == "200" and d.get("available") is False:
    print(d.get("reason") or "That name is not available.")
elif sys.argv[2].startswith("4") and isinstance(d.get("detail"), str):
    print(d["detail"])' "$body" "$code" 2>/dev/null || true)"
    rm -f "$body"
    case "$answer" in
        available) return 0 ;;
        "")
            echo "Could not reach ${NAMES_ENDPOINT}."
            return 2
            ;;
        *)
            echo "$answer"
            return 1
            ;;
    esac
}

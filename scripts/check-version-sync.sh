#!/usr/bin/env bash
# Verify that pyproject.toml and frontend/package.json versions
# match the canonical VERSION file. Fails the build on drift.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CANONICAL_VERSION="$(cat "$REPO_ROOT/VERSION" | tr -d '[:space:]')"

echo "Canonical version (VERSION file): $CANONICAL_VERSION"

errors=0

# Check pyproject.toml
PYPROJECT_VERSION="$(grep -m1 '^version' "$REPO_ROOT/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/')"
if [ "$PYPROJECT_VERSION" != "$CANONICAL_VERSION" ]; then
    echo "MISMATCH: pyproject.toml has version \"$PYPROJECT_VERSION\" (expected \"$CANONICAL_VERSION\")"
    errors=$((errors + 1))
else
    echo "OK: pyproject.toml matches"
fi

# Check frontend/package.json
PACKAGE_VERSION="$(python3 -c "import json; print(json.load(open('$REPO_ROOT/frontend/package.json'))['version'])")"
if [ "$PACKAGE_VERSION" != "$CANONICAL_VERSION" ]; then
    echo "MISMATCH: frontend/package.json has version \"$PACKAGE_VERSION\" (expected \"$CANONICAL_VERSION\")"
    errors=$((errors + 1))
else
    echo "OK: frontend/package.json matches"
fi

# Validate semver format
if ! echo "$CANONICAL_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "ERROR: VERSION file does not contain valid semver (expected X.Y.Z, got \"$CANONICAL_VERSION\")"
    errors=$((errors + 1))
else
    echo "OK: valid semver format"
fi

# Validate min_client_versions.json is valid JSON with expected keys
if ! python3 -c "
import json, sys
data = json.load(open('$REPO_ROOT/min_client_versions.json'))
for key in ('web', 'macos', 'windows'):
    if key not in data:
        print(f'ERROR: min_client_versions.json missing key \"{key}\"')
        sys.exit(1)
    parts = data[key].split('.')
    if not all(p.isdigit() for p in parts):
        print(f'ERROR: min_client_versions.json[\"{key}\"] is not valid semver: \"{data[key]}\"')
        sys.exit(1)
print('OK: min_client_versions.json is valid')
"; then
    errors=$((errors + 1))
fi

if [ "$errors" -gt 0 ]; then
    echo ""
    echo "FAILED: $errors version sync error(s) found."
    echo "Fix: update the drifted files to match the VERSION file."
    exit 1
fi

echo ""
echo "All version checks passed."

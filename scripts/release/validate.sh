#!/usr/bin/env sh
# Run release gates from a clean, disposable Python environment. Container
# validation is included when Docker is available; set SKIP_CONTAINERS=1 only
# for constrained developer environments, never for a release candidate.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
VENV="$ROOT/.release-venv"
cleanup() {
  rm -rf "$VENV"
}
trap cleanup EXIT HUP INT TERM

cd "$ROOT"

PNPM="npx --yes pnpm@9.12.0"
$PNPM install --frozen-lockfile
$PNPM run check:web
$PNPM run lint:web
$PNPM run test:web
$PNPM run build:web

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
  --require-hashes -r services/api/requirements-dev.lock.txt
PYTHONPATH="$ROOT/services/api:$ROOT/services/engine" \
  "$VENV/bin/python" -m pytest -q services/api/tests
make -C services/engine/hypermesh_core clean native-test

if [ "${SKIP_CONTAINERS:-0}" = "1" ]; then
  echo "Container validation skipped because SKIP_CONTAINERS=1."
  exit 0
fi

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required for the container release gate (or set SKIP_CONTAINERS=1)." >&2
  exit 1
}
export MESHAGENT_OIDC_ISSUER="${MESHAGENT_OIDC_ISSUER:-https://identity.example.invalid}"
export MESHAGENT_OIDC_AUDIENCE="${MESHAGENT_OIDC_AUDIENCE:-meshagent-api}"
export MESHAGENT_OIDC_CLIENT_ID="${MESHAGENT_OIDC_CLIENT_ID:-meshagent-web}"
export MESHAGENT_ANALYST_GROUPS="${MESHAGENT_ANALYST_GROUPS:-meshagent-security}"
export MESHAGENT_CISO_GROUPS="${MESHAGENT_CISO_GROUPS:-meshagent-ciso}"
export MESHAGENT_CORS_ORIGINS="${MESHAGENT_CORS_ORIGINS:-https://meshagent.example.invalid}"
export MESHAGENT_WEB_URL="${MESHAGENT_WEB_URL:-https://meshagent.example.invalid}"
export MESHAGENT_WEB_PORT="${MESHAGENT_WEB_PORT:-8080}"
docker compose -f docker-compose.production.yml --profile production config >/dev/null
mkdir -p release-artifacts
docker build --pull=false -f services/api/Dockerfile -t meshagent-api:release-validation .
docker build --pull=false \
  --build-arg "VITE_OIDC_ISSUER=$MESHAGENT_OIDC_ISSUER" \
  --build-arg "VITE_OIDC_CLIENT_ID=$MESHAGENT_OIDC_CLIENT_ID" \
  --build-arg "VITE_OIDC_SCOPE=${MESHAGENT_OIDC_SCOPE:-openid profile email}" \
  -f apps/web/Dockerfile -t meshagent-web:release-validation .
docker save meshagent-api:release-validation -o release-artifacts/meshagent-api.tar
docker save meshagent-web:release-validation -o release-artifacts/meshagent-web.tar
(
  cd release-artifacts
  sha256sum meshagent-api.tar meshagent-web.tar > SHA256SUMS
  sha256sum --check SHA256SUMS
)

#!/usr/bin/env bash
# Run the test suite in Docker against the oldest supported and the latest Home Assistant,
# the same matrix as .github/workflows/tests.yml. Works on Linux, macOS and Windows (Git Bash).
#
# Usage:
#   scripts/test-docker.sh                 # both HA versions
#   scripts/test-docker.sh min -k options  # one version ("min" or "latest"), extra pytest args
#   scripts/test-docker.sh --cov           # with a coverage report
#   REBUILD=1 scripts/test-docker.sh       # rebuild the images anyway
#
# The pins come from the same places as in CI: "min" from the matrix in
# .github/workflows/tests.yml, "latest" from requirements_test.txt. Each image is tagged
# with a hash of its pins, so a bumped pin builds a new image by itself.
#
# The repository is mounted read-only and copied inside the container (without .venv
# and .git), so no caches or .pyc files end up in your working tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKFLOW="$ROOT/.github/workflows/tests.yml"
REQUIREMENTS="$ROOT/requirements_test.txt"
# Docker Desktop on Windows needs a Windows path for bind mounts.
MOUNT="$ROOT"
if pwd -W >/dev/null 2>&1; then MOUNT="$(cd "$ROOT" && pwd -W)"; fi
export MSYS_NO_PATHCONV=1

min_python="$(grep -m1 -o 'python: "[0-9.]*"' "$WORKFLOW" | grep -o '[0-9.]*')"
min_harness="$(grep -m1 -o 'pytest-homeassistant-custom-component==[0-9.]*' "$WORKFLOW")"

targets=(min latest)
if [[ $# -gt 0 && ( $1 == min || $1 == latest ) ]]; then
  targets=("$1")
  shift
fi

status=0
for name in "${targets[@]}"; do
  if [[ $name == min ]]; then
    python="$min_python"
    # The min harness, plus everything else in requirements_test.txt.
    requirements="$min_harness"$'\n'"$(grep -v '^pytest-homeassistant-custom-component' "$REQUIREMENTS")"
  else
    python=3.14
    requirements="$(cat "$REQUIREMENTS")"
  fi
  tag="$(printf '%s\n%s' "$python" "$requirements" | sha256sum | cut -c1-12)"
  image="ha-youtube-on-tv-test:$name-$tag"

  if [[ -n ${REBUILD:-} ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    echo ">>> Building $image (Python $python)"
    # A build context holding only the requirements, not the whole repository.
    # The full image, not -slim: on 2026.3, lru-dict has no wheel for Python
    # 3.14 and has to be compiled.
    context="$(mktemp -d)"
    printf '%s\n' "$requirements" >"$context/requirements.txt"
    cat >"$context/Dockerfile" <<EOF
FROM python:$python
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt
EOF
    if pwd -W >/dev/null 2>&1; then
      docker build -q -t "$image" "$(cd "$context" && pwd -W)" >/dev/null
    else
      docker build -q -t "$image" "$context" >/dev/null
    fi
    rm -rf "$context"
  fi

  echo ">>> Testing against HA $name"
  docker run --rm -v "$MOUNT:/src:ro" "$image" bash -c '
    mkdir /work &&
    tar -C /src --exclude=./.venv --exclude=./.git -cf - . | tar -C /work -xf - &&
    cd /work &&
    python -c "import homeassistant.const as c; print(\"Home Assistant\", c.__version__)" &&
    pytest -p no:cacheprovider -q "$@"' _ "$@" || status=1
done
exit $status

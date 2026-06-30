#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/publish-pypi.sh [options]

Build and publish the current recodex package to PyPI.

Options:
  -y, --yes             Publish without interactive confirmation.
      --skip-tests      Skip unit tests.
      --dry-run-only    Build and run uv publish --dry-run, then stop.
      --allow-existing  Do not fail if this version already exists on PyPI.
      --allow-dirty     Allow publishing from a dirty git worktree.
  -h, --help            Show this help.

Environment:
  UV_PUBLISH_TOKEN or RECODEX_PYPI_TOKEN must be set for real publishing.

Example:
  export UV_PUBLISH_TOKEN='pypi-...'
  scripts/publish-pypi.sh
EOF
}

yes=0
skip_tests=0
dry_run_only=0
allow_existing=0
allow_dirty=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      yes=1
      ;;
    --skip-tests)
      skip_tests=1
      ;;
    --dry-run-only)
      dry_run_only=1
      ;;
    --allow-existing)
      allow_existing=1
      ;;
    --allow-dirty)
      allow_dirty=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

require_cmd uv
require_cmd python3

PROJECT_NAME="$(sed -n 's/^name = "\(.*\)"/\1/p' pyproject.toml | head -n 1)"
PROJECT_VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)"

if [ -z "$PROJECT_NAME" ] || [ -z "$PROJECT_VERSION" ]; then
  echo "Could not read project name/version from pyproject.toml" >&2
  exit 1
fi

echo "Package: ${PROJECT_NAME} ${PROJECT_VERSION}"

if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  dirty_status="$(git status --short)"
  if [ -n "$dirty_status" ] && [ "$allow_dirty" -eq 0 ]; then
    echo
    echo "Git worktree has uncommitted changes:"
    echo "$dirty_status"
    if [ "$yes" -eq 1 ]; then
      echo "Refusing to publish with --yes from a dirty worktree. Use --allow-dirty to override." >&2
      exit 1
    fi
    echo
    printf "Continue anyway? [y/N] "
    read -r answer
    case "$answer" in
      y|Y|yes|YES) ;;
      *) echo "Aborted."; exit 1 ;;
    esac
  fi
fi

if [ "$allow_existing" -eq 0 ] && command -v curl >/dev/null 2>&1; then
  echo "Checking whether ${PROJECT_NAME} ${PROJECT_VERSION} already exists on PyPI..."
  status_code="$(curl -s -o /dev/null -w "%{http_code}" "https://pypi.org/pypi/${PROJECT_NAME}/${PROJECT_VERSION}/json" || true)"
  if [ "$status_code" = "200" ]; then
    echo "${PROJECT_NAME} ${PROJECT_VERSION} already exists on PyPI." >&2
    echo "Bump the version in pyproject.toml before publishing again." >&2
    exit 1
  fi
fi

if [ "$skip_tests" -eq 0 ]; then
  echo "Running tests..."
  PYTHONPATH=src python3 -m unittest discover -s tests
else
  echo "Skipping tests."
fi

echo "Cleaning previous build outputs..."
rm -rf dist build

echo "Building package..."
uv build

echo "Running publish dry-run..."
uv publish --dry-run dist/*

if [ "$dry_run_only" -eq 1 ]; then
  echo "Dry-run completed. No package was uploaded."
  exit 0
fi

PUBLISH_TOKEN="${UV_PUBLISH_TOKEN:-${RECODEX_PYPI_TOKEN:-}}"
if [ -z "$PUBLISH_TOKEN" ]; then
  cat >&2 <<'EOF'
Missing PyPI token.

Set one of these environment variables before publishing:
  export UV_PUBLISH_TOKEN='pypi-...'
  export RECODEX_PYPI_TOKEN='pypi-...'
EOF
  exit 1
fi

if [ "$yes" -eq 0 ]; then
  if [ ! -t 0 ]; then
    echo "Refusing to publish without confirmation in a non-interactive shell. Use --yes to override." >&2
    exit 1
  fi
  echo
  printf "Publish ${PROJECT_NAME} ${PROJECT_VERSION} to PyPI? [y/N] "
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

echo "Publishing to PyPI..."
UV_PUBLISH_TOKEN="$PUBLISH_TOKEN" uv publish dist/*

echo
echo "Published: https://pypi.org/project/${PROJECT_NAME}/${PROJECT_VERSION}/"

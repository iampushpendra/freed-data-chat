#!/bin/bash
# Run ON THE MAC. Pushes the freed-data-chat env vars into this Vercel
# project's "production" environment.
#
# For each var: if it's already exported in the calling shell, use that value
# silently (non-interactive/CI use). Otherwise prompt for it interactively
# (CHAT_COOKIE_SECRET offers a generated default). Existing values are
# removed first (`vercel env rm ... -y`) so re-running this script updates
# rather than duplicates/conflicts; rm failures (e.g. var not yet set) are
# tolerated.
#
# Takes no args.

set -uo pipefail

VARS=(
  OPENAI_API_KEY
  OPENAI_MODEL
  CHAT_PASSWORD
  CHAT_COOKIE_SECRET
  QUERY_API_URL
  QUERY_API_SECRET
)

DEFAULT_QUERY_API_URL="http://34.100.177.202:8157/query"

echo "=== Freed Data Chat — set Vercel production env vars ==="

if ! command -v vercel >/dev/null 2>&1; then
  echo "ERROR: 'vercel' CLI not found on PATH. Install it (npm i -g vercel) and 'vercel login' first." >&2
  exit 1
fi

prompt_for() {
  # $1 = var name; echoes the value to use (does not export)
  local name="$1" value="" default=""

  case "$name" in
    CHAT_COOKIE_SECRET)
      default="$(openssl rand -hex 32 2>/dev/null || true)"
      ;;
    QUERY_API_URL)
      default="$DEFAULT_QUERY_API_URL"
      ;;
    OPENAI_MODEL)
      default="gpt-4o"
      ;;
  esac

  if [ -n "$default" ]; then
    read -r -p "$name [$default]: " value
    value="${value:-$default}"
  else
    read -r -p "$name: " value
  fi

  printf '%s' "$value"
}

set_var() {
  local name="$1" value="$2"

  if [ -z "$value" ]; then
    echo "  WARNING: $name is empty, skipping."
    return
  fi

  echo "--- $name ---"
  # Tolerate rm failure (var may not exist yet).
  vercel env rm "$name" production -y >/dev/null 2>&1 || true

  if printf '%s' "$value" | vercel env add "$name" production >/dev/null 2>&1; then
    echo "  set."
  else
    # Retry once without swallowing output, so the user sees the real error.
    echo "  retrying with output shown:"
    printf '%s' "$value" | vercel env add "$name" production
  fi
}

for name in "${VARS[@]}"; do
  existing="${!name:-}"
  if [ -n "$existing" ]; then
    echo "$name: using value from already-exported environment variable."
    value="$existing"
  else
    value="$(prompt_for "$name")"
  fi
  set_var "$name" "$value"
done

echo ""
echo "=== Done. Run 'vercel --prod' to deploy with the new env. ==="

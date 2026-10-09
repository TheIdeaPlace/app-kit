#!/usr/bin/env bash
# Tests relay/scripts/check-app-keys.sh. Needs jq (present on GitHub's Ubuntu runners).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
check="$here/../scripts/check-app-keys.sh"
failures=0

expect() {
  local want="$1" value="$2" got
  if printf '%s' "$value" | bash "$check"; then got=accept; else got=reject; fi
  if [ "$got" = "$want" ]; then
    echo "  ok   $want: $value"
  else
    echo "  FAIL expected $want, got $got: $value"
    failures=$((failures + 1))
  fi
}

expect accept '{"thechatplace":"0123456789abcdef"}'
expect accept '{"thechatplace":"0123456789abcdef","quickmail":"fedcba9876543210"}'
expect reject '{}'
expect reject '{"TheChatPlace":"0123456789abcdef"}'
expect reject '{"the-chat-place":"0123456789abcdef"}'
expect reject '{"thechatplace":"short"}'
expect reject '{"thechatplace":1234567890123456789}'
expect reject '[]'
expect reject '"0123456789abcdef"'
expect reject 'null'
expect reject 'not json'

[ "$failures" -eq 0 ] && echo "all passed" || { echo "$failures failure(s)"; exit 1; }

#!/usr/bin/env bash
# Checks a RELAY_APP_KEYS value (read from stdin) before it's pushed to the Worker.
# Exits 0 if the value is usable.
#
# - It must be a non-empty JSON object. An empty one would refuse every report.
# - Every id must be lower case, because the Worker lower-cases the id it receives.
# - Every id must be one of the apps in src/index.js, so a typo can't hide.
# - Every key must be at least 16 plain characters. A stray newline or space would never
#   match the key built into the app.
here="$(cd "$(dirname "$0")" && pwd)"
allowed="$(node "$here/app-ids.mjs")" || exit 1

jq -e --argjson allowed "$allowed" 'type == "object" and length > 0
       and (keys | all(test("\\A[a-z0-9]+\\z") and (. as $id | $allowed | any(. == $id))))
       and (to_entries | all(.value | type == "string" and test("\\A[A-Za-z0-9_-]{16,}\\z")))' > /dev/null

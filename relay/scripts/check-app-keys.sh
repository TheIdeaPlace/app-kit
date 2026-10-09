#!/usr/bin/env bash
# Checks a RELAY_APP_KEYS value (read from stdin) before it's pushed to the Worker.
# The Worker lower-cases the app id it receives, so every id must be lower case, and an empty
# object would refuse every report. Exits 0 if the value is usable.
jq -e 'type == "object" and length > 0
       and (keys | all(test("^[a-z0-9]+$")))
       and (to_entries | all(.value | type == "string" and length >= 16))' > /dev/null

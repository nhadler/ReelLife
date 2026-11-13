#!/bin/sh
# The 100% branch-coverage floor: timecode,
# importers, cue sheet exporters and the authz layer. Run after a test
# run with --cov; `just cov` and CI both call this, so the list lives
# only here. (The 90% floor overall is fail_under in pyproject.toml.)
set -eu

FILES="cues/timecode.py"
FILES="$FILES,cues/importers/*"
FILES="$FILES,cues/exporters/cue_sheet/*"
FILES="$FILES,accounts/authz.py"
FILES="$FILES,accounts/roles.py"
FILES="$FILES,reeltime/admin.py"
FILES="$FILES,mcp_server/auth.py"

exec uv run coverage report --fail-under=100 --include="$FILES"

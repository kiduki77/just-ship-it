#!/bin/bash
# Cloud sessions start from a fresh container: install the pstack plugin that CLAUDE.md's workflow uses.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

if claude plugin list 2>/dev/null | grep -q 'pstack@pstack-claude'; then
  exit 0
fi

claude plugin marketplace add michael-denyer/pstack-claude >&2
claude plugin install pstack@pstack-claude >&2

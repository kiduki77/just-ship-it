# Engineer sandbox: Claude Code + the pstack plugin, run once per agent call with only the job's clone mounted.
# Built by `ship sandbox build`. Auth is the short-lived access token of the host's Claude login, passed at
# run time as CLAUDE_CODE_OAUTH_TOKEN by name (runtime/host_auth.py); never a `claude setup-token` token, and
# no host credentials, SSH keys, git remotes or docker socket are ever mounted. chromium and the CJK fonts let the
# engineer look at the screens it builds, and the worker photograph a delivered branch for the CPO's acceptance
# (engineering.worker.capture_screens), without a browser download that the sandbox egress would refuse.
# The core daily flow and the screens run the branch's scripts/ci-setup.sh here, which the tests run in the spec's test
# image (python:3.12-slim has `python`): python-is-python3 makes `python` answer here too ([재평가] Sam: "python: not
# found" stopped a build whose 76 tests passed).
FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=2.1.312
# pstack@pstack-claude 0.9.114. The release has no git tag, so the pin is its commit.
ARG PSTACK_REF=5851c89613e2ddc3a6b7f4af02a42363b30ef8d3

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl unzip python3 python3-venv python3-pip python-is-python3 build-essential jq ripgrep \
        chromium fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system safe.directory '*' \
    && git config --system core.autocrlf false
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} && npm cache clean --force
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

RUN useradd -m -u 1001 -s /bin/bash agent
USER agent
ENV HOME=/home/agent PATH=/usr/local/bin:/usr/bin:/bin
# `claude plugin marketplace add owner/repo#ref` accepts only a branch or tag, so a local clone pins the commit.
# `claude plugin` writes ~/.claude.json with a machine and user ID; removing it gives each container its own.
RUN git init -q /home/agent/pstack-claude \
    && git -C /home/agent/pstack-claude fetch -q --depth 1 https://github.com/michael-denyer/pstack-claude.git ${PSTACK_REF} \
    && git -C /home/agent/pstack-claude checkout -q FETCH_HEAD \
    && test "$(git -C /home/agent/pstack-claude rev-parse HEAD)" = "${PSTACK_REF}" \
    && claude plugin marketplace add /home/agent/pstack-claude \
    && claude plugin install pstack@pstack-claude \
    && rm -rf /home/agent/.claude.json /home/agent/.claude/backups
WORKDIR /work

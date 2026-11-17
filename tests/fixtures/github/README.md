Recorded GitHub API replies for the collector tests.

- `repo.json`, `issues.json`, `releases.json` and the `*.unauth.json` replies were recorded on 2026-11-03 from
  `astral-sh/uv` without a token, then cut down to the fields the collector reads (issues: 30 newest, some are pull requests).
  `*.unauth.json` is what GitHub answers an unauthenticated call to the stargazers list and the traffic endpoints: 401.
- `stargazers_page*.json`, `traffic_*.json`, `referrers.json`, `paths.json` are written by hand in the shape GitHub documents
  (docs.github.com/en/rest/activity/starring, /metrics/traffic). They need a token the recording did not have.

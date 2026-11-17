"""The skip marker for tests that start the analysis sandbox image with a bind mount."""
import subprocess
import tempfile
from pathlib import Path

import pytest
from justshipit.sandbox import docker

IMAGE = "ship/analysis:1"


def _mounts_reach_this_filesystem() -> bool:
    """False when this process runs in a container that talks to the host's docker daemon: a path it binds names a
    directory on the host, so the sandbox sees none of the files the test wrote. Plain docker, not `docker.run`: that
    labels the container from the database, which does not exist yet at collection."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "marker").write_text("x")
        try:
            probe = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{Path(d).resolve()}:/probe:ro",
                                    IMAGE, "test", "-f", "/probe/marker"], capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0


def _skip_reason() -> str | None:
    if not (docker.docker_ok() and docker.image_present(IMAGE)):
        return "analysis sandbox image not built"
    if not _mounts_reach_this_filesystem():
        return "bind-mount paths do not resolve here (docker daemon is on the host, not in this container)"
    return None


_reason = _skip_reason()
needs_image = pytest.mark.skipif(_reason is not None, reason=_reason or "")

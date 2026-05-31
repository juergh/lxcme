"""lxcme-work: wrapper for mounting $PWD into LXC instances with refcounting."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

import click
import pylxd

from lxcme.users import get_tracked_mounts, sync_mounts

WORK_CONFIG_PREFIX = "user.lxcme.work."


def _configure_logging() -> None:
    """Configure logging to show INFO level messages."""
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


def compute_work_hash(path: str) -> str:
    """Return first 8 chars of SHA256 hash of path."""
    return hashlib.sha256(path.encode()).hexdigest()[:8]


def get_refcount(instance: pylxd.models.Instance, work_hash: str) -> int:
    """Get current refcount from instance config, 0 if not set."""
    key = f"{WORK_CONFIG_PREFIX}{work_hash}.count"
    return int(instance.config.get(key, 0))


def set_refcount(instance: pylxd.models.Instance, work_hash: str, count: int) -> None:
    """Set refcount in instance config, remove key if count <= 0."""
    key = f"{WORK_CONFIG_PREFIX}{work_hash}.count"
    if count <= 0:
        instance.config.pop(key, None)
    else:
        instance.config[key] = str(count)
    instance.save(wait=True)


def increment_refcount(instance: pylxd.models.Instance, work_hash: str) -> int:
    """Increment refcount, return new value."""
    count = get_refcount(instance, work_hash) + 1
    set_refcount(instance, work_hash, count)
    return count


def decrement_refcount(instance: pylxd.models.Instance, work_hash: str) -> int:
    """Decrement refcount, return new value."""
    count = get_refcount(instance, work_hash) - 1
    set_refcount(instance, work_hash, count)
    return count


@click.command()
@click.option(
    "--home",
    "home_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help=f"Host directory to mount as $HOME inside the instance (default: {Path.home()}).",
)
@click.argument("instance_name", required=True)
def main(home_dir: Path | None, instance_name: str) -> None:
    """Enter LXC instance with $PWD mounted at /work-<hash>."""
    _configure_logging()

    home_mount = home_dir if home_dir is not None else Path.home()

    cwd = os.getcwd()
    work_hash = compute_work_hash(cwd)
    work_path = f"/work-{work_hash}"

    client = pylxd.Client()

    try:
        instance = client.instances.get(instance_name)
        instance.sync()
        increment_refcount(instance, work_hash)
    except pylxd.exceptions.NotFound:
        result = subprocess.run(
            ["lxcme", instance_name, "--mount", f"{home_mount}:{Path.home()}", "--", "true"]
        )
        if result.returncode != 0:
            sys.exit(result.returncode)
        try:
            instance = client.instances.get(instance_name)
        except pylxd.exceptions.NotFound:
            sys.exit(0)
        instance.sync()
        set_refcount(instance, work_hash, 1)

    cmd = [
        "lxcme", instance_name, "--wait",
        "--mount", f"add:{cwd}:{work_path}",
        "--cwd", work_path,
    ]

    exit_code = 0
    try:
        result = subprocess.run(cmd)
        exit_code = result.returncode
    finally:
        instance.sync()
        final_count = decrement_refcount(instance, work_hash)
        if final_count <= 0:
            current_mounts = get_tracked_mounts(instance)
            new_mounts = [(h, i) for h, i in current_mounts if h != cwd]
            sync_mounts(instance, new_mounts)

    sys.exit(exit_code)

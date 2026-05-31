"""lxcme-work: wrapper for mounting $PWD into LXC instances with refcounting."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import click
import pylxd

WORK_CONFIG_PREFIX = "user.lxcme.work."


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
@click.argument("instance_name", required=True)
def main(instance_name: str) -> None:
    """Enter LXC instance with $PWD mounted at /work-<hash>."""
    scratch_dir = Path.home() / "scratch"
    if not scratch_dir.is_dir():
        click.echo(f"Error: {scratch_dir} does not exist", err=True)
        sys.exit(1)

    cwd = os.getcwd()
    work_hash = compute_work_hash(cwd)
    work_path = f"/work-{work_hash}"

    client = pylxd.Client()

    try:
        instance = client.instances.get(instance_name)
    except pylxd.exceptions.NotFound:
        click.echo(f"Error: instance '{instance_name}' not found", err=True)
        sys.exit(1)

    instance.sync()
    increment_refcount(instance, work_hash)

    cmd = [
        "lxcme", instance_name, "--wait",
        "--mount", f"{scratch_dir}:{Path.home()}",
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
            subprocess.run(
                ["lxcme", instance_name, "--mount", f"del:{cwd}"],
                capture_output=True,
            )

    sys.exit(exit_code)

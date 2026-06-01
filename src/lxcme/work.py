"""lxcme-work: wrapper for mounting $PWD into LXC instances with refcounting."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path

import click
import pylxd

from lxcme.images import ensure_image
from lxcme.instances import (
    create_instance,
    ensure_running,
    exec_interactive_wait,
    find_instance,
)
from lxcme.target import get_target_info
from lxcme.users import (
    User,
    get_current_user,
    get_instance_user_ids,
    get_tracked_mounts,
    is_setup_done,
    setup_instance_user,
    sync_mounts,
)

WORK_CONFIG_PREFIX = "user.lxcme.work."

logger = logging.getLogger(__name__)


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


def _create_and_setup_instance(
    client: pylxd.Client,
    instance_name: str,
    user: User,
    home_mount: str,
) -> pylxd.models.Instance | None:
    """Create a new instance with home mount, prompting for confirmation.

    Returns the instance if created, or None if user aborted.
    """
    target = get_target_info(None, None, None)
    initial_mounts = [(str(home_mount), str(Path.home()))]
    mount_summary = ", ".join(f"{h}:{i}" for h, i in initial_mounts)

    click.echo(
        f"Instance '{instance_name}' does not exist.\n"
        f"  Image  : {target.image_alias}\n"
        f"  Distro : {target.distro} {target.release} ({target.arch})\n"
        f"  User   : {user.username} (uid={user.uid}, gid={user.gid})\n"
        f"  Mounts : {mount_summary}"
    )
    if not click.confirm("Launch new instance?", default=False):
        click.echo("Aborted.")
        return None

    image = ensure_image(client, target.distro, target.release, target.image_alias)
    instance = create_instance(client, instance_name, image)
    setup_instance_user(instance, user)
    sync_mounts(instance, initial_mounts)
    ensure_running(instance)

    return instance


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

    cwd_resolved = os.path.realpath(cwd)
    home_resolved = os.path.realpath(home_mount)
    same_path = cwd_resolved == home_resolved

    work_hash = compute_work_hash(cwd)
    work_path = f"/work-{work_hash}"

    client = pylxd.Client()
    user = get_current_user()

    instance = find_instance(client, instance_name)
    is_new = instance is None

    if is_new:
        instance = _create_and_setup_instance(client, instance_name, user, home_resolved)
        if instance is None:
            sys.exit(0)
        if not same_path:
            set_refcount(instance, work_hash, 1)
    else:
        assert instance is not None
        instance.sync()
        if not is_setup_done(instance):
            setup_instance_user(instance, user)
        if not same_path:
            increment_refcount(instance, work_hash)

    assert instance is not None

    ensure_running(instance)

    if not same_path:
        current = get_tracked_mounts(instance)
        if not any(h == cwd for h, _ in current):
            desired = current + [(cwd, work_path)]
            sync_mounts(instance, desired)

    instance_uid, instance_gid = get_instance_user_ids(instance)
    effective_cwd = str(Path.home()) if same_path else work_path

    target = get_target_info(None, None, None)
    extra_env: dict[str, str] = {}
    if target.distro in ("debian", "ubuntu"):
        extra_env["debian_chroot"] = "lxc"

    exit_code = 0
    try:
        exit_code = exec_interactive_wait(
            instance_name,
            user,
            ["bash", "--login"],
            instance_uid,
            instance_gid,
            as_root=False,
            extra_env=extra_env,
            cwd=effective_cwd,
        )
    finally:
        if not same_path:
            instance.sync()
            final_count = decrement_refcount(instance, work_hash)
            if final_count <= 0:
                current_mounts = get_tracked_mounts(instance)
                new_mounts = [(h, i) for h, i in current_mounts if h != cwd]
                sync_mounts(instance, new_mounts)

    sys.exit(exit_code)

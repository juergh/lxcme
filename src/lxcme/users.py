"""User and group setup inside LXC instances for first-launch provisioning."""

from __future__ import annotations

import grp
import hashlib
import logging
import os
import pwd
import re
from dataclasses import dataclass
from pathlib import Path

import pylxd.exceptions
import pylxd.models

logger = logging.getLogger(__name__)

# Config key used to mark that first-launch user setup has been completed
SETUP_DONE_KEY = "user.lxcme.setup-done"
INSTANCE_UID_KEY = "user.lxcme.uid"
INSTANCE_GID_KEY = "user.lxcme.gid"
DEVICE_PREFIX = "lxcme_"

# Reports which candidate paths are referenced by processes in the instance.
# Candidates are passed as positional arguments after $0.
_WORK_USAGE_PROBE = r"""
[ -d /proc/1 ] || exit 11
command -v readlink >/dev/null 2>&1 || exit 13
[ $# -gt 0 ] || exit 0

matches() {
  target=${1%" (deleted)"}
  shift
  for candidate in "$@"; do
    case "$target" in
      "$candidate"|"$candidate"/*) printf '%s\n' "$candidate"; return ;;
    esac
  done
}

for proc in /proc/[0-9]*; do
  for entry in cwd root exe; do
    target=$(readlink "$proc/$entry" 2>/dev/null) || continue
    matches "$target" "$@"
  done
  for fd in "$proc"/fd/*; do
    target=$(readlink "$fd" 2>/dev/null) || continue
    matches "$target" "$@"
  done
done
exit 0
"""


@dataclass(frozen=True)
class User:
    """Current user's identity and home directory."""

    username: str
    uid: int
    gid: int
    groupname: str
    home: Path


def get_current_user() -> User:
    """Detect current host user's identity from the OS."""
    pw = pwd.getpwuid(os.getuid())
    gr = grp.getgrgid(os.getgid())
    return User(
        username=pw.pw_name,
        uid=pw.pw_uid,
        gid=gr.gr_gid,
        groupname=gr.gr_name,
        home=Path(pw.pw_dir),
    )


def _exec_in(
    instance: pylxd.models.Instance,
    command: list[str],
    *,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """Run command as root inside instance, returning (exit_code, stdout, stderr)."""
    result = instance.execute(command, user=0, cwd=cwd)
    return result.exit_code, result.stdout or "", result.stderr or ""


def _lookup_instance_uid(instance: pylxd.models.Instance, username: str) -> int | None:
    """Return uid of user inside instance, or None if not found."""
    rc, stdout, _ = _exec_in(instance, ["id", "-u", username])
    if rc != 0:
        return None
    try:
        return int(stdout.strip())
    except ValueError:
        return None


def _lookup_instance_gid(instance: pylxd.models.Instance, groupname: str) -> int | None:
    """Return gid of group inside instance, or None if not found."""
    rc, stdout, _ = _exec_in(instance, ["getent", "group", groupname])
    if rc != 0:
        return None
    # getent group format: name:password:gid:members
    parts = stdout.strip().split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def ensure_group(instance: pylxd.models.Instance, groupname: str) -> int:
    """Ensure group exists inside instance, creating if absent."""
    gid = _lookup_instance_gid(instance, groupname)
    if gid is not None:
        logger.debug("Group '%s' already exists in instance (gid=%d).", groupname, gid)
        return gid

    rc, _, stderr = _exec_in(instance, ["groupadd", groupname])
    if rc != 0:
        raise RuntimeError(f"Failed to create group '{groupname}' in instance: {stderr.strip()}")

    gid = _lookup_instance_gid(instance, groupname)
    if gid is None:
        raise RuntimeError(f"Group '{groupname}' was created but could not be looked up.")
    logger.info("Created group '%s' in instance (gid=%d).", groupname, gid)
    return gid


def ensure_user(instance: pylxd.models.Instance, username: str, groupname: str) -> int:
    """Ensure user exists inside instance, creating if absent."""
    uid = _lookup_instance_uid(instance, username)
    if uid is not None:
        logger.debug("User '%s' already exists in instance (uid=%d).", username, uid)
        return uid

    rc, _, stderr = _exec_in(
        instance,
        ["useradd", "--no-create-home", "--gid", groupname, username],
    )
    if rc != 0:
        raise RuntimeError(f"Failed to create user '{username}' in instance: {stderr.strip()}")

    uid = _lookup_instance_uid(instance, username)
    if uid is None:
        raise RuntimeError(f"User '{username}' was created but could not be looked up.")
    logger.info("Created user '%s' in instance (uid=%d).", username, uid)
    return uid


def configure_idmap(
    instance: pylxd.models.Instance,
    user: User,
    instance_uid: int,
    instance_gid: int,
) -> None:
    """Configure raw.idmap to map host uid/gid to instance uid/gid."""
    idmap = f"uid {user.uid} {instance_uid}\ngid {user.gid} {instance_gid}"

    # Use patch() to update only raw.idmap without touching volatile keys
    instance.patch({"config": {"raw.idmap": idmap}}, wait=True)

    logger.info("Configured raw.idmap: %s", idmap.replace("\n", " | "))


def mount_conflict_message(instance_name: str, instance_path: str, existing_host: str, new_host: str) -> str:
    """Build the message for an attempt to replace an existing mount."""
    return (
        f"Instance '{instance_name}' already has '{existing_host}' mounted at '{instance_path}'. "
        f"Refusing to replace it with '{new_host}'. Use --clear-mounts to replace mounts."
    )


def _mount_device_name(instance_path: str) -> str:
    """Derive a safe, unique LXD device name from an instance path."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance_path.strip("/")) or "root"
    digest = hashlib.sha256(instance_path.encode()).hexdigest()[:8]
    return f"{DEVICE_PREFIX}{slug[:48]}_{digest}"


def get_tracked_mounts(instance: pylxd.models.Instance) -> list[tuple[str, str]]:
    """Return currently tracked mounts as (host_path, instance_path) pairs."""
    mounts = []
    for name, dev in instance.devices.items():
        if name.startswith(DEVICE_PREFIX) and dev.get("type") == "disk":
            source = dev.get("source")
            path = dev.get("path")
            if source is not None and path is not None:
                mounts.append((source, path))
            else:
                logger.debug("Ignoring malformed disk device '%s'.", name)
    return mounts


def find_in_use_work_mounts(instance: pylxd.models.Instance, candidates: set[str]) -> set[str]:
    """Return the subset of work mount paths referenced by instance processes."""
    if not candidates:
        return set()

    command = ["/bin/sh", "-c", _WORK_USAGE_PROBE, "lxcme-probe", *sorted(candidates)]
    rc, stdout, stderr = _exec_in(instance, command, cwd="/")
    if rc != 0:
        detail = stderr.strip() or f"exit code {rc}"
        raise RuntimeError(f"Failed to inspect work mount usage in instance '{instance.name}': {detail}")
    return set(stdout.splitlines()) & candidates


def sync_mounts(
    instance: pylxd.models.Instance,
    mounts: list[tuple[str, str]],
    *,
    replace: bool = False,
) -> bool:
    """Reconcile instance disk devices against desired mounts.

    Each mount is a (host_path, instance_path) pair. Devices are identified
    by the lxcme_ prefix. Existing mounts are never replaced implicitly unless
    replace is True; conflicting mounts raise ValueError. Returns True if any
    change was made.
    """
    desired_pairs: dict[str, str] = {}
    for host_path, instance_path in mounts:
        existing_host = desired_pairs.get(instance_path)
        if existing_host is not None and existing_host != host_path:
            raise ValueError(
                f"Conflicting mounts for instance path '{instance_path}': '{existing_host}' and '{host_path}'."
            )
        desired_pairs[instance_path] = host_path

    tracked_names = {
        name for name, dev in instance.devices.items() if name.startswith(DEVICE_PREFIX) and dev.get("type") == "disk"
    }
    current: dict[str, tuple[str, str]] = {
        name: (dev["source"], dev["path"])
        for name, dev in instance.devices.items()
        if name in tracked_names and "source" in dev and "path" in dev
    }

    current_by_path: dict[str, tuple[str, tuple[str, str]]] = {}
    if not replace:
        for name, pair in current.items():
            instance_path = pair[1]
            if instance_path in current_by_path:
                raise ValueError(f"Multiple devices mount instance path '{instance_path}'.")
            current_by_path[instance_path] = (name, pair)

    desired: dict[str, tuple[str, str]] = {}
    for instance_path, host_path in desired_pairs.items():
        pair = (host_path, instance_path)
        current_entry = current_by_path.get(instance_path)
        if current_entry is not None:
            name, current_pair = current_entry
            if current_pair != pair:
                raise ValueError(mount_conflict_message(instance.name, instance_path, current_pair[0], host_path))
            desired[name] = pair
            continue

        name = _mount_device_name(instance_path)
        if (name in current and not replace) or name in desired:
            raise ValueError(f"Device name collision for instance path '{instance_path}'.")
        desired[name] = pair

    if desired == current and set(desired) == tracked_names:
        return False

    for name in tracked_names:
        if name not in desired:
            instance.devices.pop(name, None)
            logger.info("Detached device '%s'.", name)

    for name, (host_path, instance_path) in desired.items():
        if current.get(name) == (host_path, instance_path):
            continue
        instance.devices[name] = {"type": "disk", "source": host_path, "path": instance_path}
        logger.info("Attached '%s' -> '%s' as '%s'.", host_path, instance_path, name)

    instance.save(wait=True)
    instance.sync()
    return True


def _sudoers_filename(username: str) -> str:
    """Derive a safe /etc/sudoers.d filename from a username."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", username) or "lxcme-user"


def setup_passwordless_sudo(instance: pylxd.models.Instance, username: str) -> None:
    """Configure passwordless sudo for user inside instance."""
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", username):
        raise RuntimeError(f"Unsupported username for sudoers: {username!r}")
    sudoers_entry = f"{username} ALL=(ALL) NOPASSWD:ALL\n"
    sudoers_path = f"/etc/sudoers.d/{_sudoers_filename(username)}"

    rc, _, stderr = _exec_in(instance, ["mkdir", "-p", "/etc/sudoers.d"])
    if rc != 0:
        raise RuntimeError(f"Failed to create /etc/sudoers.d: {stderr.strip()}")

    try:
        instance.files.put(sudoers_path, sudoers_entry, mode=0o440)
    except (OSError, pylxd.exceptions.LXDAPIException) as exc:
        raise RuntimeError(f"Failed to write sudoers file: {exc}") from exc

    rc, _, stderr = _exec_in(instance, ["visudo", "-cf", sudoers_path])
    if rc != 0:
        raise RuntimeError(f"Invalid sudoers file '{sudoers_path}': {stderr.strip()}")

    # Best-effort: add to sudo group (may not exist on all distros)
    rc, _, stderr = _exec_in(instance, ["usermod", "-aG", "sudo", username])
    if rc != 0:
        logger.debug("Could not add '%s' to the sudo group: %s", username, stderr.strip())

    logger.info("Configured passwordless sudo for user '%s'.", username)


def is_setup_done(instance: pylxd.models.Instance) -> bool:
    """Check whether first-launch user setup has been performed."""
    instance.sync()
    return (
        str(instance.config.get(SETUP_DONE_KEY, "")) == "true"
        and INSTANCE_UID_KEY in instance.config
        and INSTANCE_GID_KEY in instance.config
    )


def mark_setup_done(instance: pylxd.models.Instance, instance_uid: int, instance_gid: int) -> None:
    """Mark first-launch setup complete and persist instance uid/gid."""
    instance.config[SETUP_DONE_KEY] = "true"
    instance.config[INSTANCE_UID_KEY] = str(instance_uid)
    instance.config[INSTANCE_GID_KEY] = str(instance_gid)
    instance.save(wait=True)


def get_instance_user_ids(instance: pylxd.models.Instance) -> tuple[int, int]:
    """Retrieve stored instance uid/gid from instance config.

    Callers must ensure the instance is synced before calling this function.
    """
    try:
        uid = int(instance.config[INSTANCE_UID_KEY])
        gid = int(instance.config[INSTANCE_GID_KEY])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Instance '{instance.name}' is missing stored uid/gid; re-run first-launch setup.") from exc
    return uid, gid


def setup_instance_user(
    instance: pylxd.models.Instance,
    user: User,
) -> None:
    """Perform full first-launch user provisioning for instance."""
    logger.info("Starting first-launch user setup for instance '%s'...", instance.name)

    # Step 1: ensure instance is running for user/group introspection and in-instance setup
    instance.sync()
    if instance.status == "Frozen":
        instance.unfreeze(wait=True)
    elif instance.status != "Running":
        instance.start(wait=True)

    # Step 2: ensure group and user
    instance_gid = ensure_group(instance, user.groupname)
    instance_uid = ensure_user(instance, user.username, user.groupname)

    # Step 3: passwordless sudo
    setup_passwordless_sudo(instance, user.username)

    # Step 4: stop instance to apply idmap config
    instance.stop(wait=True)
    instance.sync()

    # Step 5: write idmap config (must be applied while stopped)
    configure_idmap(instance, user, instance_uid, instance_gid)

    # Step 6: mark done, persisting instance uid/gid
    mark_setup_done(instance, instance_uid, instance_gid)

    # Step 7: start instance
    instance.start(wait=True)
    logger.info("First-launch setup complete for instance '%s'.", instance.name)

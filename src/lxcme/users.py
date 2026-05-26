"""User and group setup inside LXC instances for first-launch provisioning."""

from __future__ import annotations

import grp
import logging
import os
import pwd
from dataclasses import dataclass
from pathlib import Path

import pylxd.models

logger = logging.getLogger(__name__)

# Config key used to mark that first-launch user setup has been completed
SETUP_DONE_KEY = "user.lxcme.setup-done"
INSTANCE_UID_KEY = "user.lxcme.uid"
INSTANCE_GID_KEY = "user.lxcme.gid"


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


def _exec_in(instance: pylxd.models.Instance, command: list[str]) -> tuple[int, str, str]:
    """Run command as root inside instance, returning (exit_code, stdout, stderr)."""
    result = instance.execute(command, user=0)
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
    instance.config["raw.idmap"] = idmap
    instance.save(wait=True)
    logger.info("Configured raw.idmap: %s", idmap.replace("\n", " | "))


def setup_home_mount(instance: pylxd.models.Instance, user: User) -> None:
    """Attach host home directory as disk device inside instance."""
    home_str = str(user.home)
    device_name = "home"
    instance.devices[device_name] = {
        "type": "disk",
        "source": home_str,
        "path": home_str,
    }
    instance.save(wait=True)
    logger.info("Attached host home '%s' as disk device in instance.", home_str)


def setup_home_directory(
    instance: pylxd.models.Instance,
    user: User,
    instance_uid: int,
    instance_gid: int,
) -> None:
    """Create an empty home directory inside the instance."""
    home_str = str(user.home)
    rc, _, stderr = _exec_in(instance, ["mkdir", "-p", home_str])
    if rc != 0:
        raise RuntimeError(f"Failed to create home directory '{home_str}': {stderr.strip()}")

    rc, _, stderr = _exec_in(instance, ["chown", f"{instance_uid}:{instance_gid}", home_str])
    if rc != 0:
        raise RuntimeError(f"Failed to chown home directory '{home_str}': {stderr.strip()}")

    logger.info("Created home directory '%s' inside instance.", home_str)


def setup_passwordless_sudo(instance: pylxd.models.Instance, username: str) -> None:
    """Configure passwordless sudo for user inside instance."""
    sudoers_entry = f"{username} ALL=(ALL) NOPASSWD:ALL\n"
    sudoers_path = f"/etc/sudoers.d/{username}"

    rc, _, stderr = _exec_in(
        instance,
        ["bash", "-c", f"echo '{sudoers_entry}' > {sudoers_path} && chmod 440 {sudoers_path}"],
    )
    if rc != 0:
        raise RuntimeError(f"Failed to write sudoers file: {stderr.strip()}")

    # Best-effort: add to sudo group (may not exist on all distros)
    _exec_in(instance, ["usermod", "-aG", "sudo", username])

    logger.info("Configured passwordless sudo for user '%s'.", username)


def is_setup_done(instance: pylxd.models.Instance) -> bool:
    """Check whether first-launch user setup has been performed."""
    instance.sync()
    return str(instance.config.get(SETUP_DONE_KEY, "")) == "true"


def mark_setup_done(instance: pylxd.models.Instance, instance_uid: int, instance_gid: int) -> None:
    """Mark first-launch setup complete and persist instance uid/gid."""
    instance.config[SETUP_DONE_KEY] = "true"
    instance.config[INSTANCE_UID_KEY] = str(instance_uid)
    instance.config[INSTANCE_GID_KEY] = str(instance_gid)
    instance.save(wait=True)


def get_instance_user_ids(instance: pylxd.models.Instance) -> tuple[int, int]:
    """Retrieve stored instance uid/gid from instance config."""
    instance.sync()
    uid = int(instance.config[INSTANCE_UID_KEY])
    gid = int(instance.config[INSTANCE_GID_KEY])
    return uid, gid


def setup_instance_user(
    instance: pylxd.models.Instance,
    user: User,
    *,
    no_home: bool = False,
) -> None:
    """Perform full first-launch user provisioning for instance."""
    logger.info("Starting first-launch user setup for instance '%s'...", instance.name)

    # Step 1: ensure instance is running for user/group introspection and in-instance setup
    instance.sync()
    if instance.status != "Running":
        instance.start(wait=True)

    # Step 2: ensure group and user
    instance_gid = ensure_group(instance, user.groupname)
    instance_uid = ensure_user(instance, user.username, user.groupname)

    # Step 3: home directory (only needed when not mounting host home)
    if no_home:
        setup_home_directory(instance, user, instance_uid, instance_gid)

    # Step 4: passwordless sudo
    setup_passwordless_sudo(instance, user.username)

    # Step 5: stop instance to apply idmap and disk device config
    instance.stop(wait=True)

    # Step 6: write idmap config (must be applied while stopped)
    configure_idmap(instance, user, instance_uid, instance_gid)

    # Step 7: attach home disk device (must be applied while stopped)
    if not no_home:
        setup_home_mount(instance, user)

    # Step 8: mark done, persisting instance uid/gid
    mark_setup_done(instance, instance_uid, instance_gid)

    # Step 9: start instance to apply idmap and disk device config
    instance.start(wait=True)
    logger.info("First-launch setup complete for instance '%s'.", instance.name)

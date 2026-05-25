"""User and group setup inside LXC instances.

Handles first-launch provisioning:
  - user/group existence check and creation
  - raw.idmap configuration to map host uid/gid to instance uid/gid
  - home directory setup (host bind-mount or empty directory)
  - passwordless sudo configuration
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pylxd.models

if TYPE_CHECKING:
    from lxcme.host import HostUser

logger = logging.getLogger(__name__)

# Config key used to mark that first-launch user setup has been completed
SETUP_DONE_KEY = "user.lxcme.setup-done"
INSTANCE_UID_KEY = "user.lxcme.uid"
INSTANCE_GID_KEY = "user.lxcme.gid"


@dataclass(frozen=True)
class InstanceUser:
    """User/group identity as it exists inside the instance."""

    uid: int
    gid: int


def _exec_in(instance: pylxd.models.Instance, command: list[str]) -> tuple[int, str, str]:
    """Run a command as root inside the instance and return (exit_code, stdout, stderr)."""
    result = instance.execute(command, user=0)
    return result.exit_code, result.stdout or "", result.stderr or ""


def _lookup_instance_uid(instance: pylxd.models.Instance, username: str) -> int | None:
    """Return the uid of a user inside the instance, or None if not found."""
    rc, stdout, _ = _exec_in(instance, ["id", "-u", username])
    if rc != 0:
        return None
    try:
        return int(stdout.strip())
    except ValueError:
        return None


def _lookup_instance_gid(instance: pylxd.models.Instance, groupname: str) -> int | None:
    """Return the gid of a group inside the instance, or None if not found."""
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
    """Ensure a group exists inside the instance, creating it if absent."""
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
    """Ensure a user exists inside the instance, creating it if absent."""
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
    host_user: HostUser,
    instance_uid: int,
    instance_gid: int,
) -> None:
    """Configure raw.idmap to map host uid/gid to instance uid/gid."""
    idmap = f"uid {host_user.uid} {instance_uid}\ngid {host_user.gid} {instance_gid}"
    instance.config["raw.idmap"] = idmap
    instance.save(wait=True)
    logger.info("Configured raw.idmap: %s", idmap.replace("\n", " | "))


def setup_home_mount(instance: pylxd.models.Instance, host_user: HostUser) -> None:
    """Attach the host home directory as a disk device inside the instance."""
    home_str = str(host_user.home)
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
    host_user: HostUser,
    instance_uid: int,
    instance_gid: int,
) -> None:
    """Create an empty home directory inside the instance."""
    home_str = str(host_user.home)
    rc, _, stderr = _exec_in(instance, ["mkdir", "-p", home_str])
    if rc != 0:
        raise RuntimeError(f"Failed to create home directory '{home_str}': {stderr.strip()}")

    rc, _, stderr = _exec_in(instance, ["chown", f"{instance_uid}:{instance_gid}", home_str])
    if rc != 0:
        raise RuntimeError(f"Failed to chown home directory '{home_str}': {stderr.strip()}")

    logger.info("Created home directory '%s' inside instance.", home_str)


def setup_passwordless_sudo(instance: pylxd.models.Instance, username: str) -> None:
    """Configure passwordless sudo for the user inside the instance.

    Writes /etc/sudoers.d/<username> with NOPASSWD:ALL. Also ensures the
    user is a member of the sudo group.
    """
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
    """Check whether first-launch user setup has already been performed."""
    instance.sync()
    return str(instance.config.get(SETUP_DONE_KEY, "")) == "true"


def mark_setup_done(instance: pylxd.models.Instance, instance_uid: int, instance_gid: int) -> None:
    """Mark first-launch user setup as complete and persist instance uid/gid in config."""
    instance.config[SETUP_DONE_KEY] = "true"
    instance.config[INSTANCE_UID_KEY] = str(instance_uid)
    instance.config[INSTANCE_GID_KEY] = str(instance_gid)
    instance.save(wait=True)


def get_instance_user_ids(instance: pylxd.models.Instance) -> tuple[int, int]:
    """Retrieve the stored instance uid/gid from the instance config."""
    instance.sync()
    uid = int(instance.config[INSTANCE_UID_KEY])
    gid = int(instance.config[INSTANCE_GID_KEY])
    return uid, gid


def setup_instance_user(
    instance: pylxd.models.Instance,
    host_user: HostUser,
    *,
    no_home: bool = False,
) -> None:
    """Perform full first-launch user provisioning for an instance.

    Steps:
      1. Start instance for user/group introspection, creation, and in-instance setup.
      2. Ensure group and user exist inside the instance.
      3. Set up home directory (empty dir, if --no-home).
      4. Configure passwordless sudo.
      5. Write raw.idmap config (host uid/gid -> instance uid/gid).
      6. Attach host home as disk device (if not --no-home).
      7. Mark setup as done.
      8. Restart instance to apply idmap and disk device config.
    """
    logger.info("Starting first-launch user setup for instance '%s'...", instance.name)

    # Step 1: start for user/group introspection and in-instance setup
    instance.start(wait=True)

    # Step 2: ensure group and user
    instance_gid = ensure_group(instance, host_user.groupname)
    instance_uid = ensure_user(instance, host_user.username, host_user.groupname)

    # Step 3: home directory (only needed when not mounting host home)
    if no_home:
        setup_home_directory(instance, host_user, instance_uid, instance_gid)

    # Step 4: passwordless sudo
    setup_passwordless_sudo(instance, host_user.username)

    # Step 5: write idmap config (applied on next start)
    configure_idmap(instance, host_user, instance_uid, instance_gid)

    # Step 6: attach home disk device (applied on next start)
    if not no_home:
        setup_home_mount(instance, host_user)

    # Step 7: mark done, persisting instance uid/gid
    mark_setup_done(instance, instance_uid, instance_gid)

    # Step 8: restart to apply idmap and disk device config
    instance.stop(wait=True)
    instance.start(wait=True)
    logger.info("First-launch setup complete for instance '%s'.", instance.name)

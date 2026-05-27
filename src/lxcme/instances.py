"""LXC instance lifecycle: find, create, start, and execute commands."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pylxd
import pylxd.models

if TYPE_CHECKING:
    from lxcme.users import User

logger = logging.getLogger(__name__)

# Sentinel config key used to detect lxcme-managed instances
LXCME_MARKER = "user.lxcme.managed"


def find_instance(client: pylxd.Client, name: str) -> pylxd.models.Instance | None:
    """Look up an existing LXC instance by name, returning None if not found."""
    try:
        return client.instances.get(name)  # noqa: PGH003
    except pylxd.exceptions.NotFound:
        return None


def create_instance(
    client: pylxd.Client,
    name: str,
    image: pylxd.models.Image,
    instance_type: str = "container",
) -> pylxd.models.Instance:
    """Create a new LXC instance from a local image (not started)."""
    fingerprint = image.fingerprint
    config: dict[str, str] = {LXCME_MARKER: "true"}

    instance = client.instances.create(
        {
            "name": name,
            "type": instance_type,
            "source": {"type": "image", "fingerprint": fingerprint},
            "config": config,
        },
        wait=True,
    )
    logger.info("Created instance '%s'.", name)
    return instance


def ensure_running(instance: pylxd.models.Instance) -> None:
    """Start the instance if it is not already running."""
    instance.sync()
    if instance.status != "Running":
        logger.info("Starting instance '%s'...", instance.name)
        instance.start(wait=True)
        logger.info("Instance '%s' is running.", instance.name)


def exec_interactive(
    instance_name: str,
    user: User,
    command: list[str],
    instance_uid: int,
    instance_gid: int,
    *,
    as_root: bool,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Replace current process with interactive lxc exec session (never returns)."""
    argv = ["lxc", "exec", instance_name]

    if not as_root:
        argv += [
            "--user",
            str(instance_uid),
            "--group",
            str(instance_gid),
            "--cwd",
            str(user.home),
            "--env",
            f"HOME={user.home}",
            "--env",
            f"USER={user.username}",
            "--env",
            f"LOGNAME={user.username}",
        ]

    for key, value in (extra_env or {}).items():
        argv += ["--env", f"{key}={value}"]

    argv += ["--"] + command
    os.execvp("lxc", argv)


def exec_noninteractive(
    instance: pylxd.models.Instance,
    command: list[str],
    user: User,
    instance_uid: int,
    instance_gid: int,
    *,
    as_root: bool,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run command non-interactively inside instance, returning (exit_code, stdout, stderr)."""
    uid = 0 if as_root else instance_uid
    gid = 0 if as_root else instance_gid
    cwd = "/" if as_root else str(user.home)
    env: dict[str, str] = (
        {}
        if as_root
        else {
            "HOME": str(user.home),
            "USER": user.username,
            "LOGNAME": user.username,
        }
    )
    if extra_env:
        env.update(extra_env)

    result = instance.execute(command, user=uid, group=gid, cwd=cwd, environment=env)
    return result.exit_code, result.stdout, result.stderr


def is_interactive(command: list[str]) -> bool:
    """Determine if command should run interactively (shell or TTY detected)."""
    shells = {"bash", "sh", "zsh", "fish", "ksh", "dash"}
    executable = Path(command[0]).name if command else ""
    return executable in shells or os.isatty(1)

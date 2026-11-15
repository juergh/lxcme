"""LXC instance lifecycle: find, create, start, and execute commands."""

from __future__ import annotations

import logging
import os
import subprocess
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
    """Start or unfreeze the instance so it is running."""
    instance.sync()
    if instance.status == "Running":
        return
    if instance.status == "Frozen":
        logger.info("Unfreezing instance '%s'...", instance.name)
        instance.unfreeze(wait=True)
        return
    if instance.status == "Error":
        raise RuntimeError(f"Instance '{instance.name}' is in an error state.")
    logger.info("Starting instance '%s'...", instance.name)
    instance.start(wait=True)
    logger.info("Instance '%s' is running.", instance.name)


def _resolved_home(user: User, home_path: str | None) -> str:
    """Return the home path to use inside the instance."""
    return home_path if home_path is not None else str(user.home)


def _effective_cwd(as_root: bool, cwd: str | None, home: str) -> str:
    """Return the working directory for a command."""
    if cwd is not None:
        return cwd
    return "/" if as_root else home


def _build_exec_argv(
    instance_name: str,
    command: list[str],
    cwd: str,
    identity: tuple[int, int] | None,
    environment: dict[str, str],
) -> list[str]:
    """Build the 'lxc exec' argv for an interactive session."""
    argv = ["lxc", "exec", instance_name, "--cwd", cwd]
    if identity is not None:
        argv += ["--user", str(identity[0]), "--group", str(identity[1])]
    for key, value in environment.items():
        argv += ["--env", f"{key}={value}"]
    return argv + ["--"] + command


def _interactive_argv(
    instance_name: str,
    user: User,
    command: list[str],
    instance_uid: int,
    instance_gid: int,
    *,
    as_root: bool,
    extra_env: dict[str, str] | None,
    cwd: str | None,
    home_path: str | None,
) -> list[str]:
    """Assemble identity, environment, and cwd into an 'lxc exec' argv."""
    home = _resolved_home(user, home_path)
    effective_cwd = _effective_cwd(as_root, cwd, home)
    identity = None if as_root else (instance_uid, instance_gid)
    environment: dict[str, str] = {}
    if not as_root:
        environment = {"HOME": home, "USER": user.username, "LOGNAME": user.username}
    if extra_env:
        environment.update(extra_env)
    return _build_exec_argv(instance_name, command, effective_cwd, identity, environment)


def exec_interactive(
    instance_name: str,
    user: User,
    command: list[str],
    instance_uid: int,
    instance_gid: int,
    *,
    as_root: bool,
    extra_env: dict[str, str] | None = None,
    cwd: str | None = None,
    home_path: str | None = None,
) -> None:
    """Replace current process with interactive lxc exec session (never returns)."""
    argv = _interactive_argv(
        instance_name,
        user,
        command,
        instance_uid,
        instance_gid,
        as_root=as_root,
        extra_env=extra_env,
        cwd=cwd,
        home_path=home_path,
    )
    try:
        os.execvp("lxc", argv)
    except FileNotFoundError as exc:
        raise RuntimeError("The 'lxc' command was not found on PATH.") from exc


def exec_interactive_wait(
    instance_name: str,
    user: User,
    command: list[str],
    instance_uid: int,
    instance_gid: int,
    *,
    as_root: bool,
    extra_env: dict[str, str] | None = None,
    cwd: str | None = None,
    home_path: str | None = None,
) -> int:
    """Run interactive lxc exec session via subprocess, returning exit code."""
    argv = _interactive_argv(
        instance_name,
        user,
        command,
        instance_uid,
        instance_gid,
        as_root=as_root,
        extra_env=extra_env,
        cwd=cwd,
        home_path=home_path,
    )
    try:
        result = subprocess.run(argv)
    except FileNotFoundError as exc:
        raise RuntimeError("The 'lxc' command was not found on PATH.") from exc
    return result.returncode


def exec_noninteractive(
    instance: pylxd.models.Instance,
    user: User,
    command: list[str],
    instance_uid: int,
    instance_gid: int,
    *,
    as_root: bool,
    extra_env: dict[str, str] | None = None,
    cwd: str | None = None,
    home_path: str | None = None,
) -> tuple[int, str, str]:
    """Run command non-interactively inside instance, returning (exit_code, stdout, stderr)."""
    uid = 0 if as_root else instance_uid
    gid = 0 if as_root else instance_gid
    home = _resolved_home(user, home_path)
    effective_cwd = _effective_cwd(as_root, cwd, home)
    env: dict[str, str] = (
        {}
        if as_root
        else {
            "HOME": home,
            "USER": user.username,
            "LOGNAME": user.username,
        }
    )
    if extra_env:
        env.update(extra_env)

    result = instance.execute(command, user=uid, group=gid, cwd=effective_cwd, environment=env)
    return result.exit_code, result.stdout, result.stderr


def is_interactive(command: list[str]) -> bool:
    """Determine if command should run interactively (shell or TTY detected)."""
    shells = {"bash", "sh", "zsh", "fish", "ksh", "dash"}
    executable = Path(command[0]).name if command else ""
    return executable in shells or os.isatty(1)

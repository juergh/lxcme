"""lxcme CLI entrypoint."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Literal

import click
import pylxd

from lxcme.images import ensure_image
from lxcme.instances import (
    create_instance,
    ensure_running,
    exec_interactive,
    exec_interactive_wait,
    exec_noninteractive,
    find_instance,
    is_interactive,
)
from lxcme.target import get_target_info
from lxcme.users import (
    get_current_user,
    get_instance_user_ids,
    get_tracked_mounts,
    is_setup_done,
    setup_instance_user,
    sync_mounts,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MountOp:
    """A single mount operation parsed from a --mount argument."""

    kind: Literal["add", "del", "del_all"]
    host_path: str
    instance_path: str


def _configure_logging(verbose: bool) -> None:
    """Configure logging level (DEBUG if verbose, otherwise INFO)."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)


def _resolve_command(command: tuple[str, ...]) -> list[str]:
    """Return command list, defaulting to bash --login if empty."""
    return list(command) if command else ["bash", "--login"]


def _parse_mount_ops(value: str) -> list[MountOp]:
    """Parse a --mount value into one or more MountOp entries.

    Supported forms:
      /host[:/inst]          -> del_all + add /host /inst
      add:/host[:/inst]      -> add /host /inst
      del:                   -> del_all
      del:/host              -> del /host
    """
    if value.startswith("add:"):
        tail = value[4:]
        host_path, sep, instance_path = tail.partition(":")
        host_path = os.path.realpath(host_path)
        return [MountOp("add", host_path, instance_path if sep else host_path)]

    if value.startswith("del:"):
        tail = value[4:]
        if not tail:
            return [MountOp("del_all", "", "")]
        host_path = os.path.realpath(tail)
        return [MountOp("del", host_path, "")]

    # Plain path: sugar for del_all + add
    host_path, sep, instance_path = value.partition(":")
    host_path = os.path.realpath(host_path)
    return [
        MountOp("del_all", "", ""),
        MountOp("add", host_path, instance_path if sep else host_path),
    ]


def _resolve_mounts(
    ops: list[MountOp],
    current: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Apply mount ops left-to-right against current tracked mounts, returning the result."""
    working: list[tuple[str, str]] = list(current)

    for op in ops:
        if op.kind == "del_all":
            working.clear()
        elif op.kind == "del":
            before = len(working)
            working = [(h, i) for h, i in working if h != op.host_path]
            if len(working) == before:
                logger.warning("del: mount not found: %s", op.host_path)
        else:  # add
            if not any(h == op.host_path for h, _ in working):
                working.append((op.host_path, op.instance_path))

    return working


def _parse_env(value: str) -> tuple[str, str]:
    """Parse a --env value into (key, value).

    Format: KEY=VALUE.
    """
    key, sep, val = value.partition("=")
    if not sep:
        raise click.BadParameter(f"expected KEY=VALUE, got {value!r}", param_hint="'--env'")
    return key, val


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--root", is_flag=True, default=False, help="Run command as root inside the instance.")
@click.option(
    "--mount",
    "mounts",
    multiple=True,
    metavar="MOUNT_SPEC",
    help=(
        "Modify instance mounts. Repeatable, applied left-to-right. "
        "Forms: /host[:/inst] (replace all), add:/host[:/inst] (append), "
        "del:/host (remove one), del: (remove all)."
    ),
)
@click.option(
    "--env",
    "env_vars",
    multiple=True,
    metavar="KEY=VALUE",
    help="Set an environment variable inside the instance. Repeatable.",
)
@click.option("--distro", default=None, metavar="DISTRO", help="Override host distribution name.")
@click.option("--release", default=None, metavar="RELEASE", help="Override host distribution release.")
@click.option("--arch", default=None, metavar="ARCH", help="Override host architecture.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.option("--cwd", "cwd", default=None, metavar="PATH", help="CWD inside the instance (default: user home dir).")
@click.option("--wait", is_flag=True, default=False, help="Wait for interactive session to exit.")
@click.argument("instance_name", required=False, default=None)
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def main(
    root: bool,
    mounts: tuple[str, ...],
    env_vars: tuple[str, ...],
    distro: str | None,
    release: str | None,
    arch: str | None,
    verbose: bool,
    cwd: str | None,
    wait: bool,
    instance_name: str | None,
    command: tuple[str, ...],
) -> None:
    """Manage and enter LXC instances with seamless user and home directory integration.

    \b
    Examples:
      lxcme                                     Enter default instance (interactive shell)
      lxcme my-box                              Enter a named instance
      lxcme -- ls -la                           Run a non-interactive command
      lxcme --root -- apt update                Run as root
      lxcme --distro ubuntu --release resolute  Use a specific distro/release

    \b
    Mounts (applied left-to-right)
      lxmce                                     Keep exisiting mounts
      lxcme --mount /data                       Replace all mounts with /data (same as: --mount del: --mount add:/data)
      lxcme --mount /host/src:/work             Replace all mounts, custom instance path
      lxcme --mount add:/data                   Append /data to existing mounts
      lxcme --mount del:/data                   Remove /data from existing mounts
      lxcme --mount del:                        Remove all mounts
      lxcme --mount del:/old --mount add:/new   Remove /old, add /new
    """
    _configure_logging(verbose)

    # Strip leading '--' separator if present
    cmd_list = list(command)
    if cmd_list and cmd_list[0] == "--":
        cmd_list = cmd_list[1:]

    target = get_target_info(distro, release, arch)
    user = get_current_user()
    instance_name = instance_name or target.instance_alias
    resolved_command = _resolve_command(tuple(cmd_list))
    mount_ops = [op for m in mounts for op in _parse_mount_ops(m)]
    parsed_env = dict(_parse_env(e) for e in env_vars)

    client = pylxd.Client()

    instance = find_instance(client, instance_name)
    is_new = instance is None

    if is_new:
        # For new instances derive initial mount list from ops (starting from empty)
        initial_mounts = _resolve_mounts(mount_ops, [])
        mount_summary = ", ".join(f"{h}:{i}" for h, i in initial_mounts) or "(none)"
        click.echo(
            f"Instance '{instance_name}' does not exist.\n"
            f"  Image  : {target.image_alias}\n"
            f"  Distro : {target.distro} {target.release} ({target.arch})\n"
            f"  User   : {user.username} (uid={user.uid}, gid={user.gid})\n"
            f"  Mounts : {mount_summary}"
        )
        if not click.confirm("Launch new instance?", default=False):
            click.echo("Aborted.")
            sys.exit(0)

        image = ensure_image(client, target.distro, target.release, target.image_alias)
        instance = create_instance(client, instance_name, image)

    assert instance is not None

    # Run first-launch setup if needed
    if not is_setup_done(instance):
        setup_instance_user(instance, user)

    ensure_running(instance)

    # Reconcile mounts only when --mount args were supplied
    if mount_ops:
        instance.sync()
        current = get_tracked_mounts(instance)
        desired = _resolve_mounts(mount_ops, current)

        sync_mounts(instance, desired)

    # Resolve uid/gid as they exist inside the instance (stored at first-launch)
    instance_uid, instance_gid = get_instance_user_ids(instance)

    if target.distro in ("debian", "ubuntu"):
        parsed_env.setdefault("debian_chroot", "lxc")

    if is_interactive(resolved_command):
        if wait:
            exit_code = exec_interactive_wait(
                instance_name,
                user,
                resolved_command,
                instance_uid,
                instance_gid,
                as_root=root,
                extra_env=parsed_env,
                cwd=cwd,
            )
            sys.exit(exit_code)
        else:
            exec_interactive(
                instance_name,
                user,
                resolved_command,
                instance_uid,
                instance_gid,
                as_root=root,
                extra_env=parsed_env,
                cwd=cwd,
            )
            # exec_interactive replaces the process; code below is unreachable
    else:
        exit_code, stdout, stderr = exec_noninteractive(
            instance,
            user,
            resolved_command,
            instance_uid,
            instance_gid,
            as_root=root,
            extra_env=parsed_env,
            cwd=cwd,
        )
        if stdout:
            click.echo(stdout, nl=False)
        if stderr:
            click.echo(stderr, nl=False, err=True)
        sys.exit(exit_code)

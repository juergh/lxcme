"""lxcme CLI entrypoint."""

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


def _configure_logging(verbose: bool) -> None:
    """Configure logging level (DEBUG if verbose, otherwise INFO)."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)


def _resolve_command(command: tuple[str, ...]) -> list[str]:
    """Return command list, defaulting to bash --login if empty."""
    return list(command) if command else ["bash", "--login"]


def _parse_mount(value: str) -> tuple[str, str]:
    """Parse a --mount value into (host_path, instance_path).

    Format: /host[:/instance]. Defaults instance_path to host_path.
    """
    host_path, sep, instance_path = value.partition(":")
    host_path = os.path.realpath(host_path)
    return host_path, instance_path if sep else host_path


def _parse_env(value: str) -> tuple[str, str]:
    """Parse a --env value into (key, value).

    Format: KEY=VALUE.
    """
    key, sep, val = value.partition("=")
    if not sep:
        raise click.BadParameter(f"expected KEY=VALUE, got {value!r}", param_hint="'--env'")
    return key, val


def _work_path_for(cwd: str) -> str:
    """Return /work-<hash> instance path for the given host cwd."""
    digest = hashlib.sha256(cwd.encode()).hexdigest()[:8]
    return f"/work-{digest}"


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--root", is_flag=True, default=False, help="Run command as root inside the instance.")
@click.option(
    "--mount",
    "mounts",
    multiple=True,
    metavar="HOST[:INST]",
    help="Add a mount (host path, optionally mapped to instance path). Repeatable.",
)
@click.option(
    "--home",
    "home_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Host directory to mount as $HOME inside the instance.",
)
@click.option(
    "--work",
    is_flag=True,
    default=False,
    help="Mount current directory at /work-<hash> inside the instance.",
)
@click.option(
    "--clear-mounts",
    is_flag=True,
    default=False,
    help="Remove all existing mounts before adding new ones.",
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
    home_dir: Path | None,
    work: bool,
    clear_mounts: bool,
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
    Mounts:
      lxcme --mount /data                       Add /data mount
      lxcme --mount /host/src:/work             Add with custom instance path
      lxcme --mount /a --mount /b               Add multiple mounts
      lxcme --home /host/home                   Mount /host/home as $HOME
      lxcme --work my-box                       Mount cwd at /work-<hash> and enter
      lxcme --clear-mounts                      Remove all mounts
      lxcme --clear-mounts --mount /data        Replace all mounts with /data
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
    parsed_mounts = [_parse_mount(m) for m in mounts]
    if home_dir is not None:
        parsed_mounts.append((os.path.realpath(str(home_dir)), str(Path.home())))
    work_path: str | None = None
    if work:
        host_cwd = os.getcwd()
        work_path = _work_path_for(host_cwd)
        parsed_mounts.append((host_cwd, work_path))
    if work_path is not None and cwd is None:
        cwd = work_path
    parsed_env = dict(_parse_env(e) for e in env_vars)

    client = pylxd.Client()

    instance = find_instance(client, instance_name)
    is_new = instance is None

    if is_new:
        mount_summary = ", ".join(f"{h}:{i}" for h, i in parsed_mounts) or "(none)"
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

    # Reconcile mounts when --mount or --clear-mounts were supplied
    if clear_mounts or parsed_mounts:
        instance.sync()
        current = [] if clear_mounts else get_tracked_mounts(instance)

        for host_path, instance_path in parsed_mounts:
            if any(h == host_path and i == instance_path for h, i in current):
                logger.warning("Mount already present: %s -> %s", host_path, instance_path)
            else:
                current.append((host_path, instance_path))

        sync_mounts(instance, current)

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

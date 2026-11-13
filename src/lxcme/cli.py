"""lxcme CLI entrypoint."""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

import click
import pylxd
import pylxd.exceptions

from lxcme import __version__
from lxcme.images import ensure_image
from lxcme.instances import (
    LXCME_MARKER,
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


class _Command(click.Command):
    """Click command that separates command arguments from lxcme options."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Split args at '--' and keep everything after it as the command."""
        if "--" in args:
            index = args.index("--")
            ctx.meta["command_after_separator"] = tuple(args[index + 1 :])
            args = args[:index]
        return super().parse_args(ctx, args)


def _handle_errors(func: Callable[..., None]) -> Callable[..., None]:
    """Translate expected runtime failures into Click errors."""

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> None:
        try:
            func(*args, **kwargs)
        except click.ClickException:
            raise
        except pylxd.exceptions.ClientConnectionFailed as exc:
            raise click.ClickException(f"Failed to connect to LXD: {exc}") from exc
        except (pylxd.exceptions.LXDAPIException, OSError, RuntimeError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def _configure_logging(verbose: bool) -> None:
    """Configure logging level (DEBUG if verbose, otherwise INFO)."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level, force=True)


def _resolve_command(command: tuple[str, ...]) -> list[str]:
    """Return command list, defaulting to bash --login if empty."""
    return list(command) if command else ["bash", "--login"]


def _parse_mount(value: str) -> tuple[str, str]:
    """Parse a --mount value into (host_path, instance_path).

    Format: /host[:/instance]. Defaults instance_path to host_path.
    """
    host_path, sep, instance_path = value.partition(":")
    host_path = os.path.realpath(os.path.expanduser(host_path))
    if not os.path.isdir(host_path):
        raise click.BadParameter(f"host path is not a directory: {host_path!r}", param_hint="'--mount'")

    if sep:
        instance_path = os.path.normpath(instance_path)
        if not instance_path.startswith("/"):
            raise click.BadParameter(
                f"instance path must be absolute: {instance_path!r}",
                param_hint="'--mount'",
            )
    else:
        instance_path = host_path
    return host_path, instance_path


def _parse_env(value: str) -> tuple[str, str]:
    """Parse a --env value into (key, value).

    Format: KEY=VALUE.
    """
    key, sep, val = value.partition("=")
    if not sep or not key or any(char.isspace() for char in key):
        raise click.BadParameter(f"expected KEY=VALUE, got {value!r}", param_hint="'--env'")
    return key, val


def _work_path_for(cwd: str) -> str:
    """Return /work-<hash> instance path for the given host cwd."""
    digest = hashlib.sha256(cwd.encode()).hexdigest()[:12]
    return f"/work-{digest}"


def _conflict_error(instance_name: str, instance_path: str, existing_host: str, new_host: str) -> click.ClickException:
    """Build an error for an attempt to replace an existing mount."""
    return click.ClickException(
        f"Instance '{instance_name}' already has '{existing_host}' mounted at '{instance_path}'. "
        f"Refusing to replace it with '{new_host}'. Use --clear-mounts to replace mounts."
    )


@click.command(cls=_Command)
@click.version_option(__version__, prog_name="lxcme")
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
    metavar="HOST",
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
    help="Remove all lxcme-managed mounts. Affects every process using the instance.",
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
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.option("--cwd", "cwd", default=None, metavar="PATH", help="CWD inside the instance.")
@click.option("--wait", is_flag=True, default=False, help="Wait for interactive session to exit.")
@click.argument("instance_name", required=False, default=None)
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
@_handle_errors
def main(
    ctx: click.Context,
    root: bool,
    mounts: tuple[str, ...],
    home_dir: Path | None,
    work: bool,
    clear_mounts: bool,
    env_vars: tuple[str, ...],
    distro: str | None,
    release: str | None,
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
      lxcme --clear-mounts                      Remove all lxcme-managed mounts
      lxcme --clear-mounts --home /host/home    Replace all mounts with a new home
    """
    _configure_logging(verbose)

    separator_command: tuple[str, ...] = ctx.meta.get("command_after_separator", ())
    cmd_list = list(command) + list(separator_command)

    target = get_target_info(distro, release)
    user = get_current_user()
    instance_name = instance_name or target.instance_alias
    resolved_command = _resolve_command(tuple(cmd_list))
    home_path = str(user.home)
    if cwd is not None and not cwd.startswith("/"):
        raise click.BadParameter(f"working directory must be absolute: {cwd!r}", param_hint="'--cwd'")

    mount_requests = [_parse_mount(m) for m in mounts]
    home_request = (os.path.realpath(str(home_dir)), home_path) if home_dir is not None else None
    if home_request is not None and any(instance_path == home_path for _, instance_path in mount_requests):
        raise click.ClickException("--home and --mount cannot both target the home directory.")

    work_request: tuple[str, str] | None = None
    if work:
        host_cwd = os.getcwd()
        work_path = _work_path_for(host_cwd)
        work_request = (host_cwd, work_path)
        if cwd is None:
            cwd = work_path

    requested_mounts = list(mount_requests)
    if home_request is not None:
        requested_mounts.append(home_request)
    if work_request is not None:
        requested_mounts.append(work_request)

    parsed_env = dict(_parse_env(e) for e in env_vars)

    client = pylxd.Client()
    instance = find_instance(client, instance_name)
    is_new = instance is None

    if instance is not None:
        if instance.config.get(LXCME_MARKER) != "true":
            raise click.ClickException(f"Instance '{instance_name}' is not managed by lxcme; refusing to modify it.")
        if instance.type != "container":
            raise click.ClickException(f"Instance '{instance_name}' is not a container.")

    if is_new:
        mount_summary = ", ".join(f"{host}:{inst}" for host, inst in requested_mounts) or "(none)"
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

    if instance is None:
        raise click.ClickException(f"Failed to find or create instance '{instance_name}'.")

    if not is_setup_done(instance):
        setup_instance_user(instance, user)

    ensure_running(instance)
    instance.sync()

    current_mounts = [] if clear_mounts else get_tracked_mounts(instance)
    desired_mounts = list(current_mounts)

    if home_request is not None:
        existing_home = [(host, path) for host, path in desired_mounts if path == home_path]
        if existing_home and existing_home != [home_request]:
            host, _ = existing_home[0]
            raise _conflict_error(instance_name, home_path, host, home_request[0])
        if not existing_home:
            desired_mounts.append(home_request)

    for request in mount_requests:
        host, path = request
        existing = [(old_host, old_path) for old_host, old_path in desired_mounts if old_path == path]
        if existing:
            if existing != [request]:
                old_host, _ = existing[0]
                raise _conflict_error(instance_name, path, old_host, host)
            logger.warning("Mount already present: %s -> %s", host, path)
        else:
            desired_mounts.append(request)

    if work_request is not None:
        host, path = work_request
        existing = [(old_host, old_path) for old_host, old_path in desired_mounts if old_path == path]
        if existing and existing != [work_request]:
            old_host, _ = existing[0]
            raise _conflict_error(instance_name, path, old_host, host)
        if not existing:
            desired_mounts.append(work_request)

    if clear_mounts or requested_mounts:
        sync_mounts(instance, desired_mounts, replace=clear_mounts)

    home_mounted = any(path == home_path for _, path in desired_mounts)
    home_for_exec = home_path if home_mounted else "/"

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
                home_path=home_for_exec,
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
                home_path=home_for_exec,
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
            home_path=home_for_exec,
        )
        if stdout:
            click.echo(stdout, nl=False)
        if stderr:
            click.echo(stderr, nl=False, err=True)
        sys.exit(exit_code)

"""lxcme CLI entrypoint."""

from __future__ import annotations

import logging
import sys

import click
import pylxd

from lxcme.host import get_host_info, get_target_info
from lxcme.images import ensure_image
from lxcme.instances import (
    create_instance,
    ensure_running,
    exec_interactive,
    exec_noninteractive,
    find_instance,
    is_interactive,
)
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

    Format: <host-path>[:<instance-path>]. If instance-path is omitted, host-path is used.
    """
    host_path, sep, instance_path = value.partition(":")
    return host_path, instance_path if sep else host_path


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--root", is_flag=True, default=False, help="Run command as root inside the instance.")
@click.option(
    "--mount",
    "mounts",
    multiple=True,
    metavar="HOST_PATH[:INSTANCE_PATH]",
    help="Mount HOST_PATH inside the instance at INSTANCE_PATH (defaults to HOST_PATH). Repeatable.",
)
@click.option("--distro", default=None, metavar="DISTRO", help="Override host distribution name.")
@click.option("--release", default=None, metavar="RELEASE", help="Override host distribution release.")
@click.option("--arch", default=None, metavar="ARCH", help="Override host architecture.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.argument("instance_name", required=False, default=None)
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def main(
    root: bool,
    mounts: tuple[str, ...],
    distro: str | None,
    release: str | None,
    arch: str | None,
    verbose: bool,
    instance_name: str | None,
    command: tuple[str, ...],
) -> None:
    """Manage and enter LXC instances with seamless user and home directory integration."""
    _configure_logging(verbose)

    # Strip leading '--' separator if present
    cmd_list = list(command)
    if cmd_list and cmd_list[0] == "--":
        cmd_list = cmd_list[1:]

    host = get_host_info()
    target = get_target_info(host, distro=distro, release=release, arch=arch)
    user = get_current_user()
    name = instance_name or target.instance_alias
    resolved_command = _resolve_command(tuple(cmd_list))
    parsed_mounts = [_parse_mount(m) for m in mounts]

    client = pylxd.Client()

    instance = find_instance(client, name)
    is_new = instance is None

    if is_new:
        mount_summary = (
            ", ".join(f"{host_path}:{instance_path}" for host_path, instance_path in parsed_mounts) or "(none)"
        )
        click.echo(
            f"Instance '{name}' does not exist.\n"
            f"  Image  : {target.image_alias}\n"
            f"  Distro : {target.distro} {target.release} ({target.arch})\n"
            f"  User   : {user.username} (uid={user.uid}, gid={user.gid})\n"
            f"  Mounts : {mount_summary}"
        )
        if not click.confirm("Launch new instance?", default=False):
            click.echo("Aborted.")
            sys.exit(0)

        image = ensure_image(client, target.distro, target.release, target.image_alias)
        instance = create_instance(client, name, image)

    assert instance is not None

    # Run first-launch setup if needed
    if not is_setup_done(instance):
        setup_instance_user(instance, user)

    ensure_running(instance)

    # On existing instances, prompt if mounts differ from what was last applied
    instance.sync()
    if not is_new and parsed_mounts != get_tracked_mounts(instance):
        current = get_tracked_mounts(instance)
        current_str = ", ".join(f"{h}:{i}" for h, i in current) or "(none)"
        desired_str = ", ".join(f"{h}:{i}" for h, i in parsed_mounts) or "(none)"
        click.echo(f"Mounts will change for instance '{name}':\n  Current : {current_str}\n  New     : {desired_str}")
        if not click.confirm("Apply new mounts? (instance will restart)", default=False):
            click.echo("Aborted.")
            sys.exit(0)

    # Reconcile mounts; restart instance if anything changed
    if sync_mounts(instance, parsed_mounts):
        instance.stop(wait=True)
        instance.sync()
        instance.start(wait=True)

    # Resolve uid/gid as they exist inside the instance (stored at first-launch)
    instance_uid, instance_gid = get_instance_user_ids(instance)

    debian_chroot = "lxc" if target.distro in ("debian", "ubuntu") else None

    if is_interactive(resolved_command):
        exec_interactive(
            name, user, resolved_command, instance_uid, instance_gid, as_root=root, debian_chroot=debian_chroot
        )
        # exec_interactive replaces the process; code below is unreachable
    else:
        exit_code, stdout, stderr = exec_noninteractive(
            instance, resolved_command, user, instance_uid, instance_gid, as_root=root
        )
        if stdout:
            click.echo(stdout, nl=False)
        if stderr:
            click.echo(stderr, nl=False, err=True)
        sys.exit(exit_code)

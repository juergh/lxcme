"""Tests for lxcme.cli module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from lxcme.cli import main
from lxcme.host import HostInfo, HostUser

_INSTANCE_IDS = (1000, 1000)


def _make_host_info(
    distro: str = "ubuntu",
    release: str = "noble",
    arch: str = "amd64",
    username: str = "alice",
    uid: int = 1000,
    gid: int = 1000,
) -> HostInfo:
    user = HostUser(
        username=username,
        uid=uid,
        gid=gid,
        groupname=username,
        home=Path(f"/home/{username}"),
    )
    return HostInfo(distro=distro, release=release, arch=arch, user=user)


def _make_instance(name: str = "ubuntu-noble-amd64", running: bool = True, setup_done: bool = True) -> MagicMock:
    inst = MagicMock()
    inst.name = name
    inst.status = "Running" if running else "Stopped"
    inst.config = {"user.lxcme.setup-done": "true"} if setup_done else {}
    inst.devices = {}
    return inst


class TestMainExistingInstance:
    def test_enters_existing_instance_interactively(self) -> None:
        runner = CliRunner()
        host = _make_host_info()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            result = runner.invoke(main, [])

        mock_exec.assert_called_once()
        assert result.exit_code == 0

    def test_runs_noninteractive_command(self) -> None:
        runner = CliRunner()
        host = _make_host_info()
        instance = _make_instance()

        exec_result = (0, "output\n", "")
        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=False),
            patch("lxcme.cli.exec_noninteractive", return_value=exec_result),
        ):
            result = runner.invoke(main, ["--", "ls", "-la"])

        assert result.exit_code == 0
        assert "output" in result.output

    def test_passes_root_flag(self) -> None:
        runner = CliRunner()
        host = _make_host_info()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            runner.invoke(main, ["--root"])

        _, kwargs = mock_exec.call_args
        assert kwargs.get("as_root") is True

    def test_instance_ids_passed_to_exec(self) -> None:
        runner = CliRunner()
        host = _make_host_info(uid=9999, gid=9999)
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_instance_user_ids", return_value=(500, 501)),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            runner.invoke(main, [])

        args = mock_exec.call_args[0]
        # instance_uid and instance_gid should be 500/501, not host's 9999/9999
        assert 500 in args
        assert 501 in args


class TestMainNewInstance:
    def test_prompts_for_confirmation(self) -> None:
        runner = CliRunner()
        host = _make_host_info()

        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
        ):
            result = runner.invoke(main, [], input="n\n")

        assert "Launch new instance?" in result.output
        assert result.exit_code == 0

    def test_aborts_on_no(self) -> None:
        runner = CliRunner()
        host = _make_host_info()

        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
        ):
            result = runner.invoke(main, [], input="n\n")

        assert "Aborted" in result.output

    def test_creates_instance_on_yes(self) -> None:
        runner = CliRunner()
        host = _make_host_info()
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(setup_done=False)

        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
            patch("lxcme.cli.ensure_image", return_value=image),
            patch("lxcme.cli.create_instance", return_value=new_instance),
            patch("lxcme.cli.is_setup_done", return_value=False),
            patch("lxcme.cli.setup_instance_user") as mock_setup,
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, [], input="y\n")

        mock_setup.assert_called_once()

    def test_no_home_flag_passed_to_setup(self) -> None:
        runner = CliRunner()
        host = _make_host_info()
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(setup_done=False)

        with (
            patch("lxcme.cli.get_host_info", return_value=host),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
            patch("lxcme.cli.ensure_image", return_value=image),
            patch("lxcme.cli.create_instance", return_value=new_instance),
            patch("lxcme.cli.is_setup_done", return_value=False),
            patch("lxcme.cli.setup_instance_user") as mock_setup,
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, ["--no-home"], input="y\n")

        _, kwargs = mock_setup.call_args
        assert kwargs.get("no_home") is True


class TestMainDistroOverrides:
    def test_distro_release_arch_overrides(self) -> None:
        runner = CliRunner()
        host = _make_host_info(distro="debian", release="bookworm", arch="arm64")

        with (
            patch("lxcme.cli.get_host_info", return_value=host) as mock_get_host,
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=_make_instance()),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, ["--distro", "debian", "--release", "bookworm", "--arch", "arm64"])

        mock_get_host.assert_called_once_with(distro="debian", release="bookworm", arch="arm64")

"""Tests for lxcme.cli module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner, Result

from lxcme.cli import _parse_mount, _work_path_for, main
from lxcme.instances import LXCME_MARKER
from lxcme.target import TargetInfo
from lxcme.users import INSTANCE_GID_KEY, INSTANCE_UID_KEY, User

_INSTANCE_IDS = (1000, 1000)
_TARGET_UBUNTU = TargetInfo(distro="ubuntu", release="noble", arch="amd64")


def _make_user(
    username: str = "alice",
    uid: int = 1000,
    gid: int = 1000,
) -> User:
    return User(
        username=username,
        uid=uid,
        gid=gid,
        groupname=username,
        home=Path(f"/home/{username}"),
    )


def _make_instance(
    name: str = "noble-amd64",
    running: bool = True,
    setup_done: bool = True,
    managed: bool = True,
    instance_type: str = "container",
) -> MagicMock:
    inst = MagicMock()
    inst.name = name
    inst.status = "Running" if running else "Stopped"
    inst.type = instance_type
    inst.config = {LXCME_MARKER: "true"}
    if setup_done:
        inst.config["user.lxcme.setup-done"] = "true"
        inst.config[INSTANCE_UID_KEY] = "1000"
        inst.config[INSTANCE_GID_KEY] = "1000"
    inst.devices = {}
    if not managed:
        inst.config.pop(LXCME_MARKER)
    return inst


class TestParseMount:
    def test_plain_path_uses_host_as_instance_path(self, tmp_path: Path) -> None:
        host, inst = _parse_mount(str(tmp_path))
        assert inst == host

    def test_explicit_instance_path(self, tmp_path: Path) -> None:
        host, inst = _parse_mount(f"{tmp_path}:/bar")
        assert inst == "/bar"
        assert host == str(tmp_path)

    def test_resolves_realpath(self, tmp_path: Path) -> None:
        target = tmp_path / "foo"
        target.mkdir()
        host, inst = _parse_mount(f"{tmp_path}/../{tmp_path.name}/foo")
        assert ".." not in host
        assert inst == host

    def test_rejects_missing_host_path(self, tmp_path: Path) -> None:
        with pytest.raises(click.BadParameter, match="not a directory"):
            _parse_mount(str(tmp_path / "missing"))

    def test_rejects_relative_instance_path(self, tmp_path: Path) -> None:
        with pytest.raises(click.BadParameter, match="absolute"):
            _parse_mount(f"{tmp_path}:relative")

    def test_normalizes_instance_path(self, tmp_path: Path) -> None:
        _, inst = _parse_mount(f"{tmp_path}:/a/../b")
        assert inst == "/b"


class TestMainExistingInstance:
    def test_enters_existing_instance_interactively(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            result = runner.invoke(main, [])

        mock_exec.assert_called_once()
        assert result.exit_code == 0

    def test_runs_noninteractive_command(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        exec_result = (0, "output\n", "")
        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=False),
            patch("lxcme.cli.exec_noninteractive", return_value=exec_result),
        ):
            result = runner.invoke(main, ["--", "ls", "-la"])

        assert result.exit_code == 0
        assert "output" in result.output

    def test_passes_root_flag(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            runner.invoke(main, ["--root"])

        _, kwargs = mock_exec.call_args
        assert kwargs.get("as_root") is True

    def test_instance_ids_passed_to_exec(self) -> None:
        runner = CliRunner()
        user = _make_user(uid=9999, gid=9999)
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=(500, 501)),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            runner.invoke(main, [])

        args = mock_exec.call_args[0]
        assert 500 in args
        assert 501 in args


class TestMainNewInstance:
    def test_prompts_for_confirmation(self) -> None:
        runner = CliRunner()
        user = _make_user()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
        ):
            result = runner.invoke(main, [], input="n\n")

        assert "Launch new instance?" in result.output
        assert result.exit_code == 0

    def test_aborts_on_no(self) -> None:
        runner = CliRunner()
        user = _make_user()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
        ):
            result = runner.invoke(main, [], input="n\n")

        assert "Aborted" in result.output

    def test_creates_instance_on_yes(self) -> None:
        runner = CliRunner()
        user = _make_user()
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(setup_done=False)

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
            patch("lxcme.cli.ensure_image", return_value=image),
            patch("lxcme.cli.create_instance", return_value=new_instance),
            patch("lxcme.cli.is_setup_done", return_value=False),
            patch("lxcme.cli.setup_instance_user") as mock_setup,
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, [], input="y\n")

        mock_setup.assert_called_once()


class TestMainDistroOverrides:
    def test_target_info_receives_overrides(self) -> None:
        runner = CliRunner()
        user = _make_user()
        debian_target = TargetInfo(distro="debian", release="bookworm", arch="arm64")

        with (
            patch("lxcme.cli.get_target_info", return_value=debian_target) as mock_get_target,
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=_make_instance(name="debian-bookworm-arm64")),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, ["--distro", "debian", "--release", "bookworm"])

        mock_get_target.assert_called_once_with("debian", "bookworm")

    def test_non_ubuntu_debian_instance_name_includes_distro(self) -> None:
        runner = CliRunner()
        user = _make_user()
        fedora_target = TargetInfo(distro="fedora", release="40", arch="amd64")
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(name="fedora-40", setup_done=False)

        with (
            patch("lxcme.cli.get_target_info", return_value=fedora_target),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
            patch("lxcme.cli.ensure_image", return_value=image),
            patch("lxcme.cli.create_instance", return_value=new_instance) as mock_create,
            patch("lxcme.cli.is_setup_done", return_value=False),
            patch("lxcme.cli.setup_instance_user"),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, ["--distro", "fedora", "--release", "40"], input="y\n")

        assert mock_create.call_args[0][1] == "fedora-40"

    def test_debian_instance_name_omits_distro(self) -> None:
        runner = CliRunner()
        user = _make_user()
        debian_target = TargetInfo(distro="debian", release="bookworm", arch="amd64")
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(name="bookworm", setup_done=False)

        with (
            patch("lxcme.cli.get_target_info", return_value=debian_target),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
            patch("lxcme.cli.ensure_image", return_value=image),
            patch("lxcme.cli.create_instance", return_value=new_instance) as mock_create,
            patch("lxcme.cli.is_setup_done", return_value=False),
            patch("lxcme.cli.setup_instance_user"),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, ["--distro", "debian", "--release", "bookworm"], input="y\n")

        assert mock_create.call_args[0][1] == "bookworm"

    def test_same_distro_instance_name_omits_distro(self) -> None:
        runner = CliRunner()
        user = _make_user()
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(name="noble", setup_done=False)

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=None),
            patch("lxcme.cli.ensure_image", return_value=image),
            patch("lxcme.cli.create_instance", return_value=new_instance) as mock_create,
            patch("lxcme.cli.is_setup_done", return_value=False),
            patch("lxcme.cli.setup_instance_user"),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, [], input="y\n")

        assert mock_create.call_args[0][1] == "noble"


class TestMainDebianChroot:
    def _run_interactive(self, target: TargetInfo) -> MagicMock:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=target),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            runner.invoke(main, [])

        return mock_exec

    def test_debian_chroot_set_for_ubuntu(self) -> None:
        mock_exec = self._run_interactive(_TARGET_UBUNTU)
        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("debian_chroot") == "lxc"

    def test_debian_chroot_set_for_debian(self) -> None:
        target = TargetInfo(distro="debian", release="bookworm", arch="amd64")
        mock_exec = self._run_interactive(target)
        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("debian_chroot") == "lxc"

    def test_debian_chroot_not_set_for_non_debian(self) -> None:
        target = TargetInfo(distro="fedora", release="40", arch="amd64")
        mock_exec = self._run_interactive(target)
        _, kwargs = mock_exec.call_args
        assert "debian_chroot" not in kwargs.get("extra_env", {})

    def test_debian_chroot_set_when_root(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            runner.invoke(main, ["--root"])

        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("debian_chroot") == "lxc"

    def test_user_supplied_env_not_overridden_by_debian_chroot(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
        ):
            runner.invoke(main, ["--env", "debian_chroot=custom"])

        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("debian_chroot") == "custom"


class TestMainEnvVars:
    def _run(self, args: list[str], *, interactive: bool = True) -> MagicMock:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()
        mock_name = "exec_interactive" if interactive else "exec_noninteractive"
        exec_result = (0, "", "")

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=interactive),
            patch(f"lxcme.cli.{mock_name}", return_value=exec_result if not interactive else None) as mock_exec,
        ):
            runner.invoke(main, args)

        return mock_exec

    def test_single_env_var_passed_to_interactive(self) -> None:
        mock_exec = self._run(["--env", "FOO=bar"])
        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("FOO") == "bar"

    def test_multiple_env_vars_passed_to_interactive(self) -> None:
        mock_exec = self._run(["--env", "FOO=bar", "--env", "BAZ=qux"])
        _, kwargs = mock_exec.call_args
        env = kwargs.get("extra_env", {})
        assert env.get("FOO") == "bar"
        assert env.get("BAZ") == "qux"

    def test_env_var_passed_to_noninteractive(self) -> None:
        mock_exec = self._run(["--env", "FOO=bar", "--", "printenv", "FOO"], interactive=False)
        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("FOO") == "bar"

    def test_no_env_vars_passes_only_implicit_env(self) -> None:
        mock_exec = self._run([])
        _, kwargs = mock_exec.call_args
        env = kwargs.get("extra_env", {})
        assert "FOO" not in env
        assert "debian_chroot" in env

    def test_env_var_with_equals_in_value(self) -> None:
        mock_exec = self._run(["--env", "TOKEN=abc=def"])
        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("TOKEN") == "abc=def"

    def test_invalid_env_var_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--env", "NOEQUALS"])
        assert result.exit_code != 0

    def test_empty_env_key_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--env", "=value"])
        assert result.exit_code != 0

    def test_version_option(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "lxcme" in result.output


class TestMainMounts:
    def _run_with_mounts(
        self,
        args: list[str],
        current_mounts: list[tuple[str, str]] | None = None,
        sync_return: bool = False,
    ) -> MagicMock:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=current_mounts or []),
            patch("lxcme.cli.sync_mounts", return_value=sync_return) as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, args, input="y\n")

        return mock_sync

    def test_mount_adds_to_existing(self, tmp_path: Path) -> None:
        host_dir = tmp_path / "foo"
        host_dir.mkdir()
        mock_sync = self._run_with_mounts(
            ["--mount", str(host_dir)],
            current_mounts=[("/old", "/old")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        mounts = args[1]
        assert ("/old", "/old") in mounts
        assert (str(host_dir), str(host_dir)) in mounts

    def test_mount_with_explicit_instance_path(self, tmp_path: Path) -> None:
        host_dir = tmp_path / "data"
        host_dir.mkdir()
        mock_sync = self._run_with_mounts(["--mount", f"{host_dir}:/inst/data"])
        _, args, _ = mock_sync.mock_calls[0]
        assert any(i == "/inst/data" for _, i in args[1])

    def test_clear_mounts_removes_all(self) -> None:
        mock_sync = self._run_with_mounts(
            ["--clear-mounts"],
            current_mounts=[("/foo", "/foo"), ("/bar", "/bar")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        assert args[1] == []

    def test_clear_mounts_then_add(self, tmp_path: Path) -> None:
        host_dir = tmp_path / "baz"
        host_dir.mkdir()
        mock_sync = self._run_with_mounts(
            ["--clear-mounts", "--mount", str(host_dir)],
            current_mounts=[("/foo", "/foo")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        mounts = args[1]
        assert not any(h == "/foo" for h, _ in mounts)
        assert (str(host_dir), str(host_dir)) in mounts

    def test_duplicate_mount_warns(self, tmp_path: Path) -> None:
        host_dir = tmp_path / "foo"
        host_dir.mkdir()
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[(str(host_dir), str(host_dir))]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
            patch("lxcme.cli.logger") as mock_logger,
        ):
            runner.invoke(main, ["--mount", str(host_dir)])

        mock_logger.warning.assert_called()

    def test_no_mounts_skips_sync_but_reads_mounts(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]) as mock_tracked,
            patch("lxcme.cli.sync_mounts") as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, [])

        mock_tracked.assert_called_once()
        mock_sync.assert_not_called()

    def test_instance_not_restarted_when_mounts_change(self, tmp_path: Path) -> None:
        """LXC disk devices can be hot-added; no restart is required."""
        host_dir = tmp_path / "foo"
        host_dir.mkdir()
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=True),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, ["--mount", str(host_dir)])

        instance.stop.assert_not_called()
        instance.start.assert_not_called()


class TestMainCwd:
    def _run(self, args: list[str], *, interactive: bool = True) -> MagicMock:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()
        mock_name = "exec_interactive" if interactive else "exec_noninteractive"
        exec_result = (0, "", "")

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=interactive),
            patch(f"lxcme.cli.{mock_name}", return_value=exec_result if not interactive else None) as mock_exec,
        ):
            runner.invoke(main, args)

        return mock_exec

    def test_cwd_passed_to_interactive(self) -> None:
        mock_exec = self._run(["--cwd", "/work"])
        _, kwargs = mock_exec.call_args
        assert kwargs.get("cwd") == "/work"

    def test_cwd_passed_to_noninteractive(self) -> None:
        mock_exec = self._run(["--cwd", "/work", "--", "pwd"], interactive=False)
        _, kwargs = mock_exec.call_args
        assert kwargs.get("cwd") == "/work"

    def test_cwd_defaults_to_none_interactive(self) -> None:
        mock_exec = self._run([])
        _, kwargs = mock_exec.call_args
        assert kwargs.get("cwd") is None

    def test_cwd_defaults_to_none_noninteractive(self) -> None:
        mock_exec = self._run(["--", "pwd"], interactive=False)
        _, kwargs = mock_exec.call_args
        assert kwargs.get("cwd") is None

    def test_cwd_passed_with_root_flag(self) -> None:
        mock_exec = self._run(["--root", "--cwd", "/tmp"])
        _, kwargs = mock_exec.call_args
        assert kwargs.get("cwd") == "/tmp"
        assert kwargs.get("as_root") is True

    def test_relative_cwd_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--cwd", "relative"])

        assert result.exit_code != 0
        assert "must be absolute" in result.output


class TestMainWait:
    def test_wait_flag_uses_subprocess_function(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
            patch("lxcme.cli.exec_interactive_wait", return_value=0) as mock_wait,
        ):
            result = runner.invoke(main, ["--wait"])

        mock_exec.assert_not_called()
        mock_wait.assert_called_once()
        assert result.exit_code == 0

    def test_wait_flag_exits_with_command_exit_code(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive_wait", return_value=42),
        ):
            result = runner.invoke(main, ["--wait"])

        assert result.exit_code == 42

    def test_no_wait_flag_uses_execvp(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
            patch("lxcme.cli.exec_interactive_wait") as mock_wait,
        ):
            runner.invoke(main, [])

        mock_exec.assert_called_once()
        mock_wait.assert_not_called()

    def test_wait_flag_ignored_for_noninteractive(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=False),
            patch("lxcme.cli.exec_noninteractive", return_value=(0, "", "")),
            patch("lxcme.cli.exec_interactive_wait") as mock_wait,
        ):
            result = runner.invoke(main, ["--wait", "--", "ls"])

        mock_wait.assert_not_called()
        assert result.exit_code == 0


class TestMainParsing:
    def _run(self, args: list[str]) -> tuple[Result, MagicMock, MagicMock]:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance) as mock_find,
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=False),
            patch("lxcme.cli.exec_noninteractive", return_value=(0, "", "")) as mock_exec,
        ):
            result = runner.invoke(main, args)

        return result, mock_find, mock_exec

    def test_command_after_separator_uses_default_instance(self) -> None:
        result, mock_find, mock_exec = self._run(["--", "ls", "-la"])

        assert result.exit_code == 0
        assert mock_find.call_args[0][1] == "noble"
        assert mock_exec.call_args[0][2] == ["ls", "-la"]

    def test_root_command_after_separator(self) -> None:
        result, mock_find, mock_exec = self._run(["--root", "--", "apt", "update"])

        assert result.exit_code == 0
        assert mock_find.call_args[0][1] == "noble"
        assert mock_exec.call_args[0][2] == ["apt", "update"]
        assert mock_exec.call_args[1]["as_root"] is True

    def test_env_and_command_after_separator(self) -> None:
        result, _, mock_exec = self._run(["--env", "FOO=bar", "--", "printenv", "FOO"])

        assert result.exit_code == 0
        assert mock_exec.call_args[0][2] == ["printenv", "FOO"]
        assert mock_exec.call_args[1]["extra_env"]["FOO"] == "bar"

    def test_instance_name_before_separator(self) -> None:
        result, mock_find, mock_exec = self._run(["my-box", "--", "ls", "-la"])

        assert result.exit_code == 0
        assert mock_find.call_args[0][1] == "my-box"
        assert mock_exec.call_args[0][2] == ["ls", "-la"]

    def test_command_without_separator_still_works(self) -> None:
        result, mock_find, mock_exec = self._run(["my-box", "ls"])

        assert result.exit_code == 0
        assert mock_find.call_args[0][1] == "my-box"
        assert mock_exec.call_args[0][2] == ["ls"]

    def test_command_options_after_separator_are_preserved(self) -> None:
        result, _, mock_exec = self._run(["my-box", "--", "ls", "-v"])

        assert result.exit_code == 0
        assert mock_exec.call_args[0][2] == ["ls", "-v"]

    def test_unknown_option_is_rejected(self) -> None:
        result, mock_find, _ = self._run(["--rooot"])

        assert result.exit_code != 0
        mock_find.assert_not_called()


class TestMainHome:
    def _run(
        self,
        args: list[str],
        current_mounts: list[tuple[str, str]] | None = None,
        *,
        interactive: bool = False,
    ) -> tuple[Result, MagicMock, MagicMock]:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()
        exec_result = (0, "", "")
        mock_name = "exec_interactive" if interactive else "exec_noninteractive"

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=current_mounts or []),
            patch("lxcme.cli.sync_mounts", return_value=False) as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=interactive),
            patch(f"lxcme.cli.{mock_name}", return_value=None if interactive else exec_result) as mock_exec,
        ):
            result = runner.invoke(main, args)

        return result, mock_sync, mock_exec

    def test_home_mount_is_added(self, tmp_path: Path) -> None:
        result, mock_sync, _ = self._run(["--home", str(tmp_path)])

        assert result.exit_code == 0
        mounts = mock_sync.call_args[0][1]
        assert (str(tmp_path), "/home/alice") in mounts

    def test_conflicting_home_is_rejected(self, tmp_path: Path) -> None:
        result, mock_sync, _ = self._run(
            ["--home", str(tmp_path)],
            current_mounts=[("/old/home", "/home/alice")],
        )

        assert result.exit_code != 0
        assert "Refusing to replace" in result.output
        mock_sync.assert_not_called()

    def test_identical_home_is_idempotent(self, tmp_path: Path) -> None:
        result, mock_sync, _ = self._run(
            ["--home", str(tmp_path)],
            current_mounts=[(str(tmp_path), "/home/alice")],
        )

        assert result.exit_code == 0
        assert mock_sync.call_args[0][1] == [(str(tmp_path), "/home/alice")]

    def test_clear_mounts_then_home_replaces_everything(self, tmp_path: Path) -> None:
        result, mock_sync, _ = self._run(
            ["--clear-mounts", "--home", str(tmp_path)],
            current_mounts=[("/old/home", "/home/alice"), ("/data", "/data")],
        )

        assert result.exit_code == 0
        assert mock_sync.call_args[0][1] == [(str(tmp_path), "/home/alice")]

    def test_home_and_mount_same_target_is_rejected(self, tmp_path: Path) -> None:
        result, mock_sync, _ = self._run(
            ["--home", str(tmp_path), "--mount", f"{tmp_path}:/home/alice"],
        )

        assert result.exit_code != 0
        mock_sync.assert_not_called()

    def test_mount_targeting_existing_home_is_rejected(self, tmp_path: Path) -> None:
        result, mock_sync, _ = self._run(
            ["--mount", f"{tmp_path}:/home/alice"],
            current_mounts=[("/old/home", "/home/alice")],
        )

        assert result.exit_code != 0
        assert "Refusing to replace" in result.output
        mock_sync.assert_not_called()

    def test_mount_at_home_path_sets_exec_home(self, tmp_path: Path) -> None:
        result, _, mock_exec = self._run(
            ["--mount", f"{tmp_path}:/home/alice"],
            interactive=True,
        )

        assert result.exit_code == 0
        assert mock_exec.call_args[1]["home_path"] == "/home/alice"

    def test_home_sets_exec_home(self, tmp_path: Path) -> None:
        result, _, mock_exec = self._run(["--home", str(tmp_path)], interactive=True)

        assert result.exit_code == 0
        assert mock_exec.call_args[1]["home_path"] == "/home/alice"

    def test_no_home_uses_root_for_exec(self) -> None:
        result, _, mock_exec = self._run([], interactive=True)

        assert result.exit_code == 0
        assert mock_exec.call_args[1]["home_path"] == "/"

    def test_persistent_home_is_detected(self) -> None:
        result, _, mock_exec = self._run(
            [],
            current_mounts=[("/old/home", "/home/alice")],
            interactive=True,
        )

        assert result.exit_code == 0
        assert mock_exec.call_args[1]["home_path"] == "/home/alice"


class TestMainWork:
    def _run(
        self,
        args: list[str],
        current_mounts: list[tuple[str, str]] | None = None,
    ) -> tuple[Result, MagicMock, MagicMock]:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=current_mounts or []),
            patch("lxcme.cli.sync_mounts", return_value=False) as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive") as mock_exec,
            patch("lxcme.cli.os.getcwd", return_value="/tmp/work"),
        ):
            result = runner.invoke(main, args)

        return result, mock_sync, mock_exec

    def test_work_mounts_cwd_and_sets_cwd(self) -> None:
        work_path = _work_path_for("/tmp/work")
        result, mock_sync, mock_exec = self._run(["--work"])

        assert result.exit_code == 0
        assert ("/tmp/work", work_path) in mock_sync.call_args[0][1]
        assert mock_exec.call_args[1]["cwd"] == work_path
        assert mock_exec.call_args[1]["home_path"] == "/"

    def test_work_conflict_is_rejected(self) -> None:
        work_path = _work_path_for("/tmp/work")
        result, mock_sync, _ = self._run(["--work"], current_mounts=[("/other", work_path)])

        assert result.exit_code != 0
        mock_sync.assert_not_called()

    def test_work_with_home_keeps_home(self, tmp_path: Path) -> None:
        work_path = _work_path_for("/tmp/work")
        result, mock_sync, mock_exec = self._run(["--work", "--home", str(tmp_path)])

        assert result.exit_code == 0
        mounts = mock_sync.call_args[0][1]
        assert ("/tmp/work", work_path) in mounts
        assert (str(tmp_path), "/home/alice") in mounts
        assert mock_exec.call_args[1]["home_path"] == "/home/alice"


class TestMainSafety:
    def _run(self, instance: MagicMock, args: list[str] | None = None) -> tuple[Result, MagicMock, MagicMock]:
        runner = CliRunner()
        user = _make_user()

        with (
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.setup_instance_user") as mock_setup,
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=False) as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            result = runner.invoke(main, args or [])

        return result, mock_setup, mock_sync

    def test_unmanaged_instance_is_rejected(self) -> None:
        result, mock_setup, mock_sync = self._run(_make_instance(managed=False))

        assert result.exit_code != 0
        assert "not managed by lxcme" in result.output
        mock_setup.assert_not_called()
        mock_sync.assert_not_called()

    def test_virtual_machine_is_rejected(self) -> None:
        result, mock_setup, _ = self._run(_make_instance(instance_type="virtual-machine"))

        assert result.exit_code != 0
        assert "not a container" in result.output
        mock_setup.assert_not_called()

    def test_clear_mounts_does_not_prompt(self) -> None:
        result, _, mock_sync = self._run(_make_instance(), ["--clear-mounts"])

        assert result.exit_code == 0
        assert mock_sync.call_args[0][1] == []

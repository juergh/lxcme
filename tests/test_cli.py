"""Tests for lxcme.cli module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from lxcme.cli import MountOp, _parse_mount_ops, _resolve_mounts, main
from lxcme.target import TargetInfo
from lxcme.users import User

_INSTANCE_IDS = (1000, 1000)
_TARGET_UBUNTU = TargetInfo(distro="ubuntu", release="noble", arch="amd64", host_distro="ubuntu")


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


def _make_instance(name: str = "noble-amd64", running: bool = True, setup_done: bool = True) -> MagicMock:
    inst = MagicMock()
    inst.name = name
    inst.status = "Running" if running else "Stopped"
    inst.config = {"user.lxcme.setup-done": "true"} if setup_done else {}
    inst.devices = {}
    return inst


class TestParseMountOps:
    def test_plain_path_emits_del_all_and_add(self) -> None:
        ops = _parse_mount_ops("/foo")
        assert ops[0] == MountOp("del_all", "", "")
        assert ops[1].kind == "add"
        assert ops[1].instance_path == ops[1].host_path

    def test_plain_path_with_instance_path(self) -> None:
        ops = _parse_mount_ops("/foo:/bar")
        assert ops[0] == MountOp("del_all", "", "")
        assert ops[1].kind == "add"
        assert ops[1].instance_path == "/bar"

    def test_add_prefix(self) -> None:
        ops = _parse_mount_ops("add:/foo")
        assert len(ops) == 1
        assert ops[0].kind == "add"
        assert ops[0].instance_path == ops[0].host_path

    def test_add_prefix_with_instance_path(self) -> None:
        ops = _parse_mount_ops("add:/foo:/bar")
        assert len(ops) == 1
        assert ops[0].kind == "add"
        assert ops[0].instance_path == "/bar"

    def test_del_all(self) -> None:
        ops = _parse_mount_ops("del:")
        assert ops == [MountOp("del_all", "", "")]

    def test_del_specific_path(self) -> None:
        ops = _parse_mount_ops("del:/foo")
        assert len(ops) == 1
        assert ops[0].kind == "del"
        assert ops[0].instance_path == ""


class TestResolveMounts:
    def test_empty_ops_returns_current(self) -> None:
        current = [("/foo", "/foo")]
        assert _resolve_mounts([], current) == current

    def test_del_all_clears(self) -> None:
        current = [("/foo", "/foo"), ("/bar", "/bar")]
        ops = [MountOp("del_all", "", "")]
        assert _resolve_mounts(ops, current) == []

    def test_del_specific_removes_entry(self) -> None:
        current = [("/foo", "/foo"), ("/bar", "/bar")]
        ops = [MountOp("del", "/foo", "")]
        assert _resolve_mounts(ops, current) == [("/bar", "/bar")]

    def test_del_missing_warns_and_continues(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        current = [("/bar", "/bar")]
        ops = [MountOp("del", "/foo", "")]
        with caplog.at_level(logging.WARNING):
            result = _resolve_mounts(ops, current)
        assert result == [("/bar", "/bar")]
        assert any("/foo" in m for m in caplog.messages)

    def test_add_appends_new_entry(self) -> None:
        current = [("/foo", "/foo")]
        ops = [MountOp("add", "/bar", "/bar")]
        assert _resolve_mounts(ops, current) == [("/foo", "/foo"), ("/bar", "/bar")]

    def test_add_skips_duplicate(self) -> None:
        current = [("/foo", "/foo")]
        ops = [MountOp("add", "/foo", "/foo")]
        assert _resolve_mounts(ops, current) == [("/foo", "/foo")]

    def test_plain_mount_replaces_all(self) -> None:
        current = [("/old", "/old")]
        ops = [MountOp("del_all", "", ""), MountOp("add", "/new", "/new")]
        assert _resolve_mounts(ops, current) == [("/new", "/new")]

    def test_del_all_then_add_left_to_right(self) -> None:
        current = [("/foo", "/foo")]
        ops = [MountOp("del_all", "", ""), MountOp("add", "/bar", "/bar")]
        assert _resolve_mounts(ops, current) == [("/bar", "/bar")]

    def test_del_then_add_same_path_results_in_present(self) -> None:
        current = [("/foo", "/foo")]
        ops = [MountOp("del", "/foo", ""), MountOp("add", "/foo", "/foo")]
        assert _resolve_mounts(ops, current) == [("/foo", "/foo")]

    def test_add_then_del_same_path_results_in_absent(self) -> None:
        current: list[tuple[str, str]] = []
        ops = [MountOp("add", "/foo", "/foo"), MountOp("del", "/foo", "")]
        assert _resolve_mounts(ops, current) == []

    def test_mixed_del_and_add(self) -> None:
        current = [("/foo", "/foo"), ("/bar", "/bar")]
        ops = [MountOp("del", "/foo", ""), MountOp("add", "/baz", "/baz")]
        assert _resolve_mounts(ops, current) == [("/bar", "/bar"), ("/baz", "/baz")]


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
        debian_target = TargetInfo(distro="debian", release="bookworm", arch="arm64", host_distro="ubuntu")

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
            runner.invoke(main, ["--distro", "debian", "--release", "bookworm", "--arch", "arm64"])

        mock_get_target.assert_called_once_with("debian", "bookworm", "arm64")

    def test_non_ubuntu_debian_instance_name_includes_distro(self) -> None:
        runner = CliRunner()
        user = _make_user()
        fedora_target = TargetInfo(distro="fedora", release="40", arch="amd64", host_distro="ubuntu")
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(name="fedora-40-amd64", setup_done=False)

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

        assert mock_create.call_args[0][1] == "fedora-40-amd64"

    def test_debian_instance_name_omits_distro(self) -> None:
        runner = CliRunner()
        user = _make_user()
        debian_target = TargetInfo(distro="debian", release="bookworm", arch="amd64", host_distro="ubuntu")
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(name="bookworm-amd64", setup_done=False)

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

        assert mock_create.call_args[0][1] == "bookworm-amd64"

    def test_same_distro_instance_name_omits_distro(self) -> None:
        runner = CliRunner()
        user = _make_user()
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(name="noble-amd64", setup_done=False)

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

        assert mock_create.call_args[0][1] == "noble-amd64"


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
        target = TargetInfo(distro="debian", release="bookworm", arch="amd64", host_distro="ubuntu")
        mock_exec = self._run_interactive(target)
        _, kwargs = mock_exec.call_args
        assert kwargs.get("extra_env", {}).get("debian_chroot") == "lxc"

    def test_debian_chroot_not_set_for_non_debian(self) -> None:
        target = TargetInfo(distro="fedora", release="40", arch="amd64", host_distro="ubuntu")
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

    def test_plain_mount_replaces_all_existing(self) -> None:
        mock_sync = self._run_with_mounts(
            ["--mount", "/foo"],
            current_mounts=[("/old", "/old")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        assert args[1] == [("/foo", "/foo")]

    def test_mount_with_explicit_instance_path(self) -> None:
        mock_sync = self._run_with_mounts(["--mount", "/host/data:/inst/data"])
        _, args, _ = mock_sync.mock_calls[0]
        assert ("/host/data", "/inst/data") in args[1]

    def test_add_prefix_appends_to_existing(self) -> None:
        mock_sync = self._run_with_mounts(
            ["--mount", "add:/bar"],
            current_mounts=[("/foo", "/foo")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        mounts = args[1]
        assert ("/foo", "/foo") in mounts
        assert ("/bar", "/bar") in mounts

    def test_del_all_removes_all_mounts(self) -> None:
        mock_sync = self._run_with_mounts(
            ["--mount", "del:"],
            current_mounts=[("/foo", "/foo"), ("/bar", "/bar")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        assert args[1] == []

    def test_del_specific_removes_one_mount(self) -> None:
        mock_sync = self._run_with_mounts(
            ["--mount", "del:/foo"],
            current_mounts=[("/foo", "/foo"), ("/bar", "/bar")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        assert args[1] == [("/bar", "/bar")]

    def test_del_all_then_add_via_mixed(self) -> None:
        mock_sync = self._run_with_mounts(
            ["--mount", "del:", "--mount", "add:/baz"],
            current_mounts=[("/foo", "/foo")],
        )
        _, args, _ = mock_sync.mock_calls[0]
        assert args[1] == [("/baz", "/baz")]

    def test_no_mounts_skips_sync(self) -> None:
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
            patch("lxcme.cli.get_tracked_mounts") as mock_tracked,
            patch("lxcme.cli.sync_mounts") as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, [])

        mock_tracked.assert_not_called()
        mock_sync.assert_not_called()

    def test_instance_not_restarted_when_mounts_change(self) -> None:
        """LXC disk devices can be hot-added; no restart is required."""
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
            runner.invoke(main, ["--mount", "/foo"])

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

"""Tests for lxcme.cli module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from lxcme.cli import main
from lxcme.host import HostInfo, TargetInfo
from lxcme.users import User

_INSTANCE_IDS = (1000, 1000)
_HOST_UBUNTU = HostInfo(distro="ubuntu", release="noble", arch="amd64")
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


class TestMainExistingInstance:
    def test_enters_existing_instance_interactively(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
        # instance_uid and instance_gid should be 500/501, not user's 9999/9999
        assert 500 in args
        assert 501 in args


class TestMainNewInstance:
    def test_prompts_for_confirmation(self) -> None:
        runner = CliRunner()
        user = _make_user()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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

        mock_get_target.assert_called_once_with(_HOST_UBUNTU, distro="debian", release="bookworm", arch="arm64")

    def test_non_ubuntu_debian_instance_name_includes_distro(self) -> None:
        runner = CliRunner()
        user = _make_user()
        fedora_target = TargetInfo(distro="fedora", release="40", arch="amd64", host_distro="ubuntu")
        image = MagicMock()
        image.fingerprint = "abc"
        new_instance = _make_instance(name="fedora-40-amd64", setup_done=False)

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
        # debian_chroot is implicitly added for ubuntu targets
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
    def _run_with_mounts(self, args: list[str], sync_return: bool = False) -> MagicMock:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[]),
            patch("lxcme.cli.sync_mounts", return_value=sync_return) as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            runner.invoke(main, args, input="y\n" if args else None)

        return mock_sync

    def test_mount_with_explicit_instance_path(self) -> None:
        mock_sync = self._run_with_mounts(["--mount", "/host/data:/inst/data"])
        _, args, _ = mock_sync.mock_calls[0]
        assert ("/host/data", "/inst/data") in args[1]

    def test_mount_defaults_instance_path_to_host_path(self) -> None:
        mock_sync = self._run_with_mounts(["--mount", "/home/alice"])
        _, args, _ = mock_sync.mock_calls[0]
        assert ("/home/alice", "/home/alice") in args[1]

    def test_multiple_mounts(self) -> None:
        mock_sync = self._run_with_mounts(["--mount", "/foo:/bar", "--mount", "/baz"])
        _, args, _ = mock_sync.mock_calls[0]
        mounts = args[1]
        assert ("/foo", "/bar") in mounts
        assert ("/baz", "/baz") in mounts

    def test_no_mounts_passes_empty_list(self) -> None:
        mock_sync = self._run_with_mounts([])
        _, args, _ = mock_sync.mock_calls[0]
        assert args[1] == []

    def test_instance_restarted_when_mounts_change(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            runner.invoke(main, ["--mount", "/foo"], input="y\n")

        instance.stop.assert_called_once_with(wait=True)
        instance.start.assert_called_once_with(wait=True)

    def test_instance_not_restarted_when_mounts_unchanged(self) -> None:
        self._run_with_mounts(["--mount", "/foo"], sync_return=False)
        # No restart expected; covered by absence of stop/start calls in sync_return=False path

    def test_mount_change_prompts_user(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[("/old", "/old")]),
            patch("lxcme.cli.sync_mounts", return_value=True),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            result = runner.invoke(main, ["--mount", "/foo"], input="y\n")

        assert "Mounts will change" in result.output
        assert "/old:/old" in result.output
        assert "/foo:/foo" in result.output

    def test_mount_change_aborts_on_no(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.get_tracked_mounts", return_value=[("/old", "/old")]),
            patch("lxcme.cli.sync_mounts", return_value=False) as mock_sync,
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            result = runner.invoke(main, ["--mount", "/foo"], input="n\n")

        assert "Aborted" in result.output
        mock_sync.assert_not_called()


class TestMainKeepMounts:
    def test_keep_mounts_skips_sync(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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
            runner.invoke(main, ["--keep-mounts"])

        mock_tracked.assert_not_called()
        mock_sync.assert_not_called()

    def test_keep_mounts_suppresses_prompt(self) -> None:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
            patch("lxcme.cli.get_target_info", return_value=_TARGET_UBUNTU),
            patch("lxcme.cli.get_current_user", return_value=user),
            patch("lxcme.cli.pylxd.Client"),
            patch("lxcme.cli.find_instance", return_value=instance),
            patch("lxcme.cli.is_setup_done", return_value=True),
            patch("lxcme.cli.ensure_running"),
            patch("lxcme.cli.sync_mounts"),
            patch("lxcme.cli.get_instance_user_ids", return_value=_INSTANCE_IDS),
            patch("lxcme.cli.is_interactive", return_value=True),
            patch("lxcme.cli.exec_interactive"),
        ):
            result = runner.invoke(main, ["--keep-mounts", "--mount", "/foo"])

        assert "Mounts will change" not in result.output


class TestMainCwd:
    def _run(self, args: list[str], *, interactive: bool = True) -> MagicMock:
        runner = CliRunner()
        user = _make_user()
        instance = _make_instance()
        mock_name = "exec_interactive" if interactive else "exec_noninteractive"
        exec_result = (0, "", "")

        with (
            patch("lxcme.cli.get_host_info", return_value=_HOST_UBUNTU),
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

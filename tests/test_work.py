"""Tests for lxcme.work module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from lxcme.work import (
    WORK_CONFIG_PREFIX,
    compute_work_hash,
    decrement_refcount,
    get_refcount,
    increment_refcount,
    main,
    set_refcount,
)


class TestComputeWorkHash:
    def test_deterministic(self) -> None:
        path = "/home/user/projects/myapp"
        assert compute_work_hash(path) == compute_work_hash(path)

    def test_length_is_8(self) -> None:
        path = "/some/path"
        assert len(compute_work_hash(path)) == 8

    def test_different_paths_different_hashes(self) -> None:
        hash1 = compute_work_hash("/path/one")
        hash2 = compute_work_hash("/path/two")
        assert hash1 != hash2

    def test_hex_characters(self) -> None:
        result = compute_work_hash("/any/path")
        assert all(c in "0123456789abcdef" for c in result)


class TestGetRefcount:
    def test_missing_key_returns_zero(self) -> None:
        instance = MagicMock()
        instance.config = {}

        assert get_refcount(instance, "abc12345") == 0

    def test_returns_stored_value(self) -> None:
        instance = MagicMock()
        instance.config = {f"{WORK_CONFIG_PREFIX}abc12345.count": "3"}

        assert get_refcount(instance, "abc12345") == 3


class TestSetRefcount:
    def test_positive_sets_key(self) -> None:
        instance = MagicMock()
        instance.config = {}

        set_refcount(instance, "abc12345", 2)

        assert instance.config[f"{WORK_CONFIG_PREFIX}abc12345.count"] == "2"
        instance.save.assert_called_once_with(wait=True)

    def test_zero_removes_key(self) -> None:
        instance = MagicMock()
        instance.config = {f"{WORK_CONFIG_PREFIX}abc12345.count": "1"}

        set_refcount(instance, "abc12345", 0)

        assert f"{WORK_CONFIG_PREFIX}abc12345.count" not in instance.config
        instance.save.assert_called_once_with(wait=True)

    def test_negative_removes_key(self) -> None:
        instance = MagicMock()
        instance.config = {f"{WORK_CONFIG_PREFIX}abc12345.count": "1"}

        set_refcount(instance, "abc12345", -1)

        assert f"{WORK_CONFIG_PREFIX}abc12345.count" not in instance.config


class TestIncrementRefcount:
    def test_from_zero(self) -> None:
        instance = MagicMock()
        instance.config = {}

        result = increment_refcount(instance, "abc12345")

        assert result == 1
        assert instance.config[f"{WORK_CONFIG_PREFIX}abc12345.count"] == "1"

    def test_from_existing(self) -> None:
        instance = MagicMock()
        instance.config = {f"{WORK_CONFIG_PREFIX}abc12345.count": "2"}

        result = increment_refcount(instance, "abc12345")

        assert result == 3
        assert instance.config[f"{WORK_CONFIG_PREFIX}abc12345.count"] == "3"


class TestDecrementRefcount:
    def test_to_zero(self) -> None:
        instance = MagicMock()
        instance.config = {f"{WORK_CONFIG_PREFIX}abc12345.count": "1"}

        result = decrement_refcount(instance, "abc12345")

        assert result == 0
        assert f"{WORK_CONFIG_PREFIX}abc12345.count" not in instance.config

    def test_to_positive(self) -> None:
        instance = MagicMock()
        instance.config = {f"{WORK_CONFIG_PREFIX}abc12345.count": "3"}

        result = decrement_refcount(instance, "abc12345")

        assert result == 2
        assert instance.config[f"{WORK_CONFIG_PREFIX}abc12345.count"] == "2"


def _make_mock_instance(config: dict[str, str] | None = None) -> MagicMock:
    """Create a mock instance with standard config."""
    instance = MagicMock()
    instance.config = config if config is not None else {}
    instance.name = "my-instance"
    return instance


def _make_mock_user(home: Path) -> MagicMock:
    """Create a mock user."""
    user = MagicMock()
    user.username = "testuser"
    user.uid = 1000
    user.gid = 1000
    user.groupname = "testuser"
    user.home = home
    return user


def _make_mock_target() -> MagicMock:
    """Create a mock target info."""
    target = MagicMock()
    target.distro = "ubuntu"
    target.release = "noble"
    target.arch = "amd64"
    target.image_alias = "noble-amd64"
    return target


class TestMain:
    def test_uses_home_as_default(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock()
        captured_mounts: list[list[tuple[str, str]]] = []

        def capture_sync_mounts(inst: MagicMock, mounts: list[tuple[str, str]]) -> bool:
            captured_mounts.append(list(mounts))
            return True

        mock_sync_mounts.side_effect = capture_sync_mounts

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=None),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.ensure_image", return_value=MagicMock()),
            patch("lxcme.work.create_instance", return_value=instance),
            patch("lxcme.work.setup_instance_user"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"], input="y\n")

        assert len(captured_mounts) >= 1
        initial_mount = captured_mounts[0]
        assert (str(tmp_path), str(tmp_path)) in initial_mount

    def test_uses_custom_home_when_specified(self, tmp_path: Path) -> None:
        runner = CliRunner()
        custom_home = tmp_path / "custom"
        custom_home.mkdir()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock()
        captured_mounts: list[list[tuple[str, str]]] = []

        def capture_sync_mounts(inst: MagicMock, mounts: list[tuple[str, str]]) -> bool:
            captured_mounts.append(list(mounts))
            return True

        mock_sync_mounts.side_effect = capture_sync_mounts

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=None),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.ensure_image", return_value=MagicMock()),
            patch("lxcme.work.create_instance", return_value=instance),
            patch("lxcme.work.setup_instance_user"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["--home", str(custom_home), "my-instance"], input="y\n")

        assert len(captured_mounts) >= 1
        initial_mount = captured_mounts[0]
        assert (str(custom_home), str(tmp_path)) in initial_mount

    def test_creates_instance_if_not_exists(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_create_instance = MagicMock(return_value=instance)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=None),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.ensure_image", return_value=MagicMock()),
            patch("lxcme.work.create_instance", mock_create_instance),
            patch("lxcme.work.setup_instance_user"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            result = runner.invoke(main, ["my-instance"], input="y\n")

        assert result.exit_code == 0
        mock_create_instance.assert_called_once()

    def test_sets_refcount_after_creation(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        work_hash = compute_work_hash("/test/path")

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=None),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.ensure_image", return_value=MagicMock()),
            patch("lxcme.work.create_instance", return_value=instance),
            patch("lxcme.work.setup_instance_user"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"], input="y\n")

        key = f"{WORK_CONFIG_PREFIX}{work_hash}.count"
        assert key not in instance.config
        assert instance.save.call_count >= 2

    def test_exits_gracefully_if_creation_aborted(self, tmp_path: Path) -> None:
        runner = CliRunner()

        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=None),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
        ):
            mock_client_class.return_value = MagicMock()
            result = runner.invoke(main, ["my-instance"], input="n\n")

        assert result.exit_code == 0
        assert "Aborted" in result.output

    def test_increments_refcount_for_existing_instance(self, tmp_path: Path) -> None:
        runner = CliRunner()

        work_hash = compute_work_hash("/test/path")
        instance = _make_mock_instance({f"{WORK_CONFIG_PREFIX}{work_hash}.count": "1"})
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        key = f"{WORK_CONFIG_PREFIX}{work_hash}.count"
        assert instance.config.get(key) == "1"
        assert instance.save.call_count >= 2

    def test_decrements_refcount_after_session(self, tmp_path: Path) -> None:
        runner = CliRunner()

        work_hash = compute_work_hash("/test/path")
        instance = _make_mock_instance({f"{WORK_CONFIG_PREFIX}{work_hash}.count": "2"})
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        key = f"{WORK_CONFIG_PREFIX}{work_hash}.count"
        assert instance.config.get(key) == "2"

    def test_unmounts_when_refcount_zero(self, tmp_path: Path) -> None:
        runner = CliRunner()

        work_hash = compute_work_hash("/test/path")
        work_path = f"/work-{work_hash}"
        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)
        home_mount = (str(tmp_path), str(tmp_path))

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[home_mount, ("/test/path", work_path)]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        assert mock_sync_mounts.call_count == 1
        final_call = mock_sync_mounts.call_args_list[-1]
        assert final_call[0][1] == [home_mount]

    def test_no_unmount_when_refcount_positive(self, tmp_path: Path) -> None:
        runner = CliRunner()

        work_hash = compute_work_hash("/test/path")
        work_path = f"/work-{work_hash}"
        instance = _make_mock_instance({f"{WORK_CONFIG_PREFIX}{work_hash}.count": "2"})
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch(
                "lxcme.work.get_tracked_mounts",
                return_value=[(str(tmp_path), str(tmp_path)), ("/test/path", work_path)],
            ),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        mock_sync_mounts.assert_not_called()

    def test_cleanup_on_nonzero_exit(self, tmp_path: Path) -> None:
        runner = CliRunner()

        work_hash = compute_work_hash("/test/path")
        work_path = f"/work-{work_hash}"
        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)
        home_mount = (str(tmp_path), str(tmp_path))

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=42),
            patch("lxcme.work.get_tracked_mounts", return_value=[home_mount, ("/test/path", work_path)]),
        ):
            mock_client_class.return_value = MagicMock()
            result = runner.invoke(main, ["my-instance"])

        assert mock_sync_mounts.call_count == 1
        final_call = mock_sync_mounts.call_args_list[-1]
        assert final_call[0][1] == [home_mount]
        assert result.exit_code == 42

    def test_calls_exec_interactive_wait_with_correct_args(self, tmp_path: Path) -> None:
        runner = CliRunner()

        work_hash = compute_work_hash("/test/project")
        work_path = f"/work-{work_hash}"
        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_exec = MagicMock(return_value=0)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/project"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", mock_exec),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        mock_exec.assert_called_once_with(
            "my-instance",
            user,
            ["bash", "--login"],
            1000,
            1000,
            as_root=False,
            extra_env={"debian_chroot": "lxc"},
            cwd=work_path,
        )

    def test_errors_if_home_does_not_exist(self, tmp_path: Path) -> None:
        runner = CliRunner()
        nonexistent = tmp_path / "nonexistent"

        result = runner.invoke(main, ["--home", str(nonexistent), "my-instance"])

        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_skips_work_mount_when_cwd_equals_home(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)
        mock_exec = MagicMock(return_value=0)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value=str(tmp_path)),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", mock_exec),
            patch("lxcme.work.get_tracked_mounts", return_value=[(str(tmp_path), str(tmp_path))]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        mock_sync_mounts.assert_not_called()

    def test_sets_cwd_to_home_when_cwd_equals_home(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_exec = MagicMock(return_value=0)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value=str(tmp_path)),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", mock_exec),
            patch("lxcme.work.get_tracked_mounts", return_value=[(str(tmp_path), str(tmp_path))]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["cwd"] == str(tmp_path)

    def test_no_refcount_when_cwd_equals_home(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value=str(tmp_path)),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[(str(tmp_path), str(tmp_path))]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        instance.save.assert_not_called()

    def test_handles_symlinked_paths(self, tmp_path: Path) -> None:
        runner = CliRunner()

        real_path = tmp_path / "real"
        real_path.mkdir()
        symlink_path = tmp_path / "symlink"
        symlink_path.symlink_to(real_path)

        instance = _make_mock_instance()
        user = _make_mock_user(real_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)
        mock_exec = MagicMock(return_value=0)

        def mock_realpath(p: str) -> str:
            if "symlink" in str(p):
                return str(real_path)
            return str(p)

        with (
            patch("lxcme.work.Path.home", return_value=real_path),
            patch("lxcme.work.os.getcwd", return_value=str(symlink_path)),
            patch("lxcme.work.os.path.realpath", side_effect=mock_realpath),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", mock_exec),
            patch("lxcme.work.get_tracked_mounts", return_value=[(str(real_path), str(real_path))]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        mock_sync_mounts.assert_not_called()
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["cwd"] == str(real_path)

    def test_no_unmount_when_cwd_equals_home(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value=str(tmp_path)),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[(str(tmp_path), str(tmp_path))]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        mock_sync_mounts.assert_not_called()

    def test_runs_setup_for_existing_instance_if_not_done(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_setup = MagicMock()

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: p),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=False),
            patch("lxcme.work.setup_instance_user", mock_setup),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", return_value=True),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        mock_setup.assert_called_once_with(instance, user)

    def test_ensures_home_mount_for_existing_instance(self, tmp_path: Path) -> None:
        runner = CliRunner()

        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)
        captured_mounts: list[list[tuple[str, str]]] = []

        def capture_sync_mounts(inst: MagicMock, mounts: list[tuple[str, str]]) -> bool:
            captured_mounts.append(list(mounts))
            return True

        mock_sync_mounts.side_effect = capture_sync_mounts

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value=str(tmp_path)),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        assert len(captured_mounts) == 1
        assert (str(tmp_path), str(tmp_path)) in captured_mounts[0]

    def test_ensures_home_mount_with_work_path(self, tmp_path: Path) -> None:
        runner = CliRunner()

        work_hash = compute_work_hash("/test/path")
        work_path = f"/work-{work_hash}"
        instance = _make_mock_instance()
        user = _make_mock_user(tmp_path)
        target = _make_mock_target()

        mock_sync_mounts = MagicMock(return_value=True)
        captured_mounts: list[list[tuple[str, str]]] = []

        def capture_sync_mounts(inst: MagicMock, mounts: list[tuple[str, str]]) -> bool:
            captured_mounts.append(list(mounts))
            return True

        mock_sync_mounts.side_effect = capture_sync_mounts

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.os.path.realpath", side_effect=lambda p: str(p)),
            patch("lxcme.work.pylxd.Client") as mock_client_class,
            patch("lxcme.work.find_instance", return_value=instance),
            patch("lxcme.work.get_current_user", return_value=user),
            patch("lxcme.work.get_target_info", return_value=target),
            patch("lxcme.work.is_setup_done", return_value=True),
            patch("lxcme.work.ensure_running"),
            patch("lxcme.work.sync_mounts", mock_sync_mounts),
            patch("lxcme.work.get_instance_user_ids", return_value=(1000, 1000)),
            patch("lxcme.work.exec_interactive_wait", return_value=0),
            patch("lxcme.work.get_tracked_mounts", return_value=[]),
        ):
            mock_client_class.return_value = MagicMock()
            runner.invoke(main, ["my-instance"])

        assert len(captured_mounts) >= 1
        assert (str(tmp_path), str(tmp_path)) in captured_mounts[0]
        assert ("/test/path", work_path) in captured_mounts[0]

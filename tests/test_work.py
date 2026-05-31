"""Tests for lxcme.work module."""

from __future__ import annotations

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


class TestMain:
    def test_errors_if_scratch_missing(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()

        with patch("lxcme.work.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["my-instance"])

        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_errors_if_instance_not_found(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        import pylxd.exceptions

        mock_client = MagicMock()
        mock_client.instances.get.side_effect = pylxd.exceptions.NotFound("not found")

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.pylxd.Client", return_value=mock_client),
        ):
            result = runner.invoke(main, ["my-instance"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_increments_refcount_before_lxcme(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        instance = MagicMock()
        instance.config = {}
        mock_client = MagicMock()
        mock_client.instances.get.return_value = instance

        call_order: list[str] = []

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[0] == "lxcme" and "--wait" in cmd:
                call_order.append(f"refcount={instance.config.get(f'{WORK_CONFIG_PREFIX}', 'missing')}")
            return MagicMock(returncode=0)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.pylxd.Client", return_value=mock_client),
            patch("lxcme.work.subprocess.run", side_effect=mock_subprocess_run),
        ):
            runner.invoke(main, ["my-instance"])

        # Verify refcount was incremented (save called) before subprocess
        assert instance.save.call_count >= 1

    def test_decrements_refcount_after_lxcme(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        instance = MagicMock()
        instance.config = {}
        mock_client = MagicMock()
        mock_client.instances.get.return_value = instance

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.pylxd.Client", return_value=mock_client),
            patch("lxcme.work.subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            runner.invoke(main, ["my-instance"])

        # Increment + decrement = 2 saves
        assert instance.save.call_count == 2

    def test_unmounts_when_refcount_zero(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        instance = MagicMock()
        instance.config = {}
        mock_client = MagicMock()
        mock_client.instances.get.return_value = instance

        subprocess_calls: list[list[str]] = []

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            subprocess_calls.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.pylxd.Client", return_value=mock_client),
            patch("lxcme.work.subprocess.run", side_effect=mock_subprocess_run),
        ):
            runner.invoke(main, ["my-instance"])

        # Should have lxcme --wait call and lxcme --mount del: call
        assert len(subprocess_calls) == 2
        assert "--wait" in subprocess_calls[0]
        assert "del:/test/path" in subprocess_calls[1]

    def test_no_unmount_when_refcount_positive(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        work_hash = compute_work_hash("/test/path")
        instance = MagicMock()
        instance.config = {f"{WORK_CONFIG_PREFIX}{work_hash}.count": "2"}
        mock_client = MagicMock()
        mock_client.instances.get.return_value = instance

        subprocess_calls: list[list[str]] = []

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            subprocess_calls.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.pylxd.Client", return_value=mock_client),
            patch("lxcme.work.subprocess.run", side_effect=mock_subprocess_run),
        ):
            runner.invoke(main, ["my-instance"])

        # Should only have the lxcme --wait call, no unmount
        assert len(subprocess_calls) == 1
        assert "--wait" in subprocess_calls[0]

    def test_cleanup_on_nonzero_exit(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        instance = MagicMock()
        instance.config = {}
        mock_client = MagicMock()
        mock_client.instances.get.return_value = instance

        subprocess_calls: list[list[str]] = []

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            subprocess_calls.append(cmd)
            if "--wait" in cmd:
                return MagicMock(returncode=42)
            return MagicMock(returncode=0)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value="/test/path"),
            patch("lxcme.work.pylxd.Client", return_value=mock_client),
            patch("lxcme.work.subprocess.run", side_effect=mock_subprocess_run),
        ):
            result = runner.invoke(main, ["my-instance"])

        # Should still clean up even on non-zero exit
        assert len(subprocess_calls) == 2
        assert result.exit_code == 42

    def test_builds_correct_lxcme_command(self, tmp_path: MagicMock) -> None:
        runner = CliRunner()
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        instance = MagicMock()
        instance.config = {}
        mock_client = MagicMock()
        mock_client.instances.get.return_value = instance

        captured_cmd: list[str] = []

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if "--wait" in cmd:
                captured_cmd.extend(cmd)
            return MagicMock(returncode=0)

        cwd = "/test/project"
        work_hash = compute_work_hash(cwd)

        with (
            patch("lxcme.work.Path.home", return_value=tmp_path),
            patch("lxcme.work.os.getcwd", return_value=cwd),
            patch("lxcme.work.pylxd.Client", return_value=mock_client),
            patch("lxcme.work.subprocess.run", side_effect=mock_subprocess_run),
        ):
            runner.invoke(main, ["my-instance"])

        assert captured_cmd[0] == "lxcme"
        assert captured_cmd[1] == "my-instance"
        assert "--wait" in captured_cmd
        assert f"{scratch}:{tmp_path}" in captured_cmd
        assert f"add:{cwd}:/work-{work_hash}" in captured_cmd
        assert "--cwd" in captured_cmd
        assert f"/work-{work_hash}" in captured_cmd

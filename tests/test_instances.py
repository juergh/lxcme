"""Tests for lxcme.instances module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lxcme.instances import (
    create_instance,
    ensure_running,
    exec_interactive,
    exec_noninteractive,
    find_instance,
    is_interactive,
)
from lxcme.users import User


def _make_user(uid: int = 1000, gid: int = 1000) -> User:
    return User(
        username="alice",
        uid=uid,
        gid=gid,
        groupname="alice",
        home=Path("/home/alice"),
    )


class TestFindInstance:
    def test_returns_instance_when_found(self) -> None:
        client = MagicMock()
        inst = MagicMock()
        client.instances.get.return_value = inst

        result = find_instance(client, "my-box")
        assert result is inst

    def test_returns_none_when_not_found(self) -> None:
        import pylxd.exceptions

        client = MagicMock()
        client.instances.get.side_effect = pylxd.exceptions.NotFound("not found")

        result = find_instance(client, "missing")
        assert result is None


class TestCreateInstance:
    def test_creates_container_by_default(self) -> None:
        client = MagicMock()
        image = MagicMock()
        image.fingerprint = "deadbeef"
        instance = MagicMock()
        client.instances.create.return_value = instance

        result = create_instance(client, "mybox", image)

        assert result is instance
        call_kwargs = client.instances.create.call_args
        config = call_kwargs[0][0]
        assert config["type"] == "container"
        assert config["source"]["fingerprint"] == "deadbeef"
        assert config["name"] == "mybox"

    def test_creates_vm_when_specified(self) -> None:
        client = MagicMock()
        image = MagicMock()
        image.fingerprint = "cafebabe"
        client.instances.create.return_value = MagicMock()

        create_instance(client, "myvm", image, instance_type="virtual-machine")

        config = client.instances.create.call_args[0][0]
        assert config["type"] == "virtual-machine"


class TestEnsureRunning:
    def test_starts_stopped_instance(self) -> None:
        instance = MagicMock()
        instance.status = "Stopped"

        ensure_running(instance)
        instance.start.assert_called_once_with(wait=True)

    def test_does_not_start_running_instance(self) -> None:
        instance = MagicMock()
        instance.status = "Running"

        ensure_running(instance)
        instance.start.assert_not_called()


class TestExecInteractive:
    def test_execvp_called_as_user(self) -> None:
        user = _make_user()
        with patch("os.execvp") as mock_exec:
            exec_interactive("mybox", user, ["bash", "--login"], 1000, 1000, as_root=False)

        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "lxc"
        argv = args[1]
        assert "mybox" in argv
        assert "--user" in argv
        assert "1000" in argv
        assert "--group" in argv
        assert "bash" in argv

    def test_execvp_uses_instance_ids_not_host_ids(self) -> None:
        user = _make_user(uid=9999, gid=9999)
        with patch("os.execvp") as mock_exec:
            exec_interactive("mybox", user, ["bash", "--login"], 1000, 1000, as_root=False)

        argv = mock_exec.call_args[0][1]
        assert "9999" not in argv
        assert "1000" in argv

    def test_execvp_called_as_root(self) -> None:
        user = _make_user()
        with patch("os.execvp") as mock_exec:
            exec_interactive("mybox", user, ["bash", "--login"], 1000, 1000, as_root=True)

        argv = mock_exec.call_args[0][1]
        assert "--user" not in argv
        assert "--group" not in argv

    def test_debian_chroot_set_as_user(self) -> None:
        user = _make_user()
        with patch("os.execvp") as mock_exec:
            exec_interactive("mybox", user, ["bash", "--login"], 1000, 1000, as_root=False, debian_chroot="lxc")

        argv = mock_exec.call_args[0][1]
        assert "--env" in argv
        idx = argv.index("debian_chroot=lxc")
        assert argv[idx - 1] == "--env"

    def test_debian_chroot_set_as_root(self) -> None:
        user = _make_user()
        with patch("os.execvp") as mock_exec:
            exec_interactive("mybox", user, ["bash", "--login"], 1000, 1000, as_root=True, debian_chroot="lxc")

        argv = mock_exec.call_args[0][1]
        assert "debian_chroot=lxc" in argv

    def test_debian_chroot_not_set_when_none(self) -> None:
        user = _make_user()
        with patch("os.execvp") as mock_exec:
            exec_interactive("mybox", user, ["bash", "--login"], 1000, 1000, as_root=False, debian_chroot=None)

        argv = mock_exec.call_args[0][1]
        assert not any("debian_chroot" in arg for arg in argv)


class TestExecNoninteractive:
    def test_runs_as_user(self) -> None:
        instance = MagicMock()
        result = MagicMock()
        result.exit_code = 0
        result.stdout = "hello\n"
        result.stderr = ""
        instance.execute.return_value = result

        user = _make_user()
        code, out, err = exec_noninteractive(instance, ["echo", "hello"], user, 1000, 1000, as_root=False)

        assert code == 0
        assert out == "hello\n"
        instance.execute.assert_called_once()
        call_kwargs = instance.execute.call_args[1]
        assert call_kwargs["user"] == 1000
        assert call_kwargs["group"] == 1000

    def test_uses_instance_ids_not_host_ids(self) -> None:
        instance = MagicMock()
        result = MagicMock()
        result.exit_code = 0
        result.stdout = ""
        result.stderr = ""
        instance.execute.return_value = result

        user = _make_user(uid=9999, gid=9999)
        exec_noninteractive(instance, ["id"], user, 1000, 1000, as_root=False)

        call_kwargs = instance.execute.call_args[1]
        assert call_kwargs["user"] == 1000
        assert call_kwargs["group"] == 1000

    def test_runs_as_root(self) -> None:
        instance = MagicMock()
        result = MagicMock()
        result.exit_code = 0
        result.stdout = ""
        result.stderr = ""
        instance.execute.return_value = result

        user = _make_user()
        exec_noninteractive(instance, ["id"], user, 1000, 1000, as_root=True)

        call_kwargs = instance.execute.call_args[1]
        assert call_kwargs["user"] == 0
        assert call_kwargs["group"] == 0


class TestIsInteractive:
    @pytest.mark.parametrize("shell", ["bash", "sh", "zsh", "fish"])
    def test_shells_are_interactive(self, shell: str) -> None:
        assert is_interactive([shell]) is True

    def test_non_shell_command_checks_tty(self) -> None:
        with patch("os.isatty", return_value=False):
            assert is_interactive(["python3", "script.py"]) is False

    def test_non_shell_interactive_when_tty(self) -> None:
        with patch("os.isatty", return_value=True):
            assert is_interactive(["python3"]) is True

    def test_full_path_shell(self) -> None:
        assert is_interactive(["/bin/bash"]) is True

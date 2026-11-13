"""Tests for lxcme.users module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lxcme.users import (
    DEVICE_PREFIX,
    INSTANCE_GID_KEY,
    INSTANCE_UID_KEY,
    SETUP_DONE_KEY,
    User,
    _mount_device_name,
    _sudoers_filename,
    configure_idmap,
    ensure_group,
    ensure_user,
    get_instance_user_ids,
    get_tracked_mounts,
    is_setup_done,
    mark_setup_done,
    setup_instance_user,
    setup_passwordless_sudo,
    sync_mounts,
)


def _make_user(uid: int = 1000, gid: int = 1000) -> User:
    return User(
        username="alice",
        uid=uid,
        gid=gid,
        groupname="alice",
        home=Path("/home/alice"),
    )


def _make_exec_result(exit_code: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.exit_code = exit_code
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestEnsureGroup:
    def test_returns_existing_gid(self) -> None:
        instance = MagicMock()
        instance.execute.return_value = _make_exec_result(stdout="alice:x:1000:\n")

        gid = ensure_group(instance, "alice")
        assert gid == 1000
        # groupadd should NOT be called
        calls = [c[0][0] for c in instance.execute.call_args_list]
        assert not any("groupadd" in str(c) for c in calls)

    def test_creates_group_when_absent(self) -> None:
        instance = MagicMock()
        # First call: getent fails (group not found), second: groupadd, third: getent success
        instance.execute.side_effect = [
            _make_exec_result(exit_code=2),  # getent - not found
            _make_exec_result(exit_code=0),  # groupadd
            _make_exec_result(stdout="alice:x:1001:\n"),  # getent after creation
        ]

        gid = ensure_group(instance, "alice")
        assert gid == 1001

    def test_raises_on_groupadd_failure(self) -> None:
        instance = MagicMock()
        instance.execute.side_effect = [
            _make_exec_result(exit_code=2),  # getent - not found
            _make_exec_result(exit_code=1, stderr="error"),  # groupadd fails
        ]

        with pytest.raises(RuntimeError, match="Failed to create group"):
            ensure_group(instance, "alice")


class TestEnsureUser:
    def test_returns_existing_uid(self) -> None:
        instance = MagicMock()
        instance.execute.return_value = _make_exec_result(stdout="1000\n")

        uid = ensure_user(instance, "alice", "alice")
        assert uid == 1000

    def test_creates_user_when_absent(self) -> None:
        instance = MagicMock()
        instance.execute.side_effect = [
            _make_exec_result(exit_code=1),  # id -u - not found
            _make_exec_result(exit_code=0),  # useradd
            _make_exec_result(stdout="1002\n"),  # id -u after creation
        ]

        uid = ensure_user(instance, "alice", "alice")
        assert uid == 1002

    def test_raises_on_useradd_failure(self) -> None:
        instance = MagicMock()
        instance.execute.side_effect = [
            _make_exec_result(exit_code=1),
            _make_exec_result(exit_code=1, stderr="useradd error"),
        ]

        with pytest.raises(RuntimeError, match="Failed to create user"):
            ensure_user(instance, "alice", "alice")


class TestConfigureIdmap:
    def test_sets_raw_idmap(self) -> None:
        instance = MagicMock()
        user = _make_user(uid=1234, gid=5678)

        configure_idmap(instance, user, instance_uid=1000, instance_gid=1000)

        instance.patch.assert_called_once()
        payload = instance.patch.call_args[0][0]
        idmap = payload["config"]["raw.idmap"]
        assert "uid 1234 1000" in idmap
        assert "gid 5678 1000" in idmap


class TestSyncMounts:
    def test_adds_new_mounts(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {}
        device_name = _mount_device_name("/inst/foo")

        changed = sync_mounts(instance, [("/host/foo", "/inst/foo")])

        assert changed is True
        assert instance.devices[device_name]["source"] == "/host/foo"
        assert instance.devices[device_name]["path"] == "/inst/foo"
        instance.save.assert_called_once_with(wait=True)
        instance.sync.assert_called_once()

    def test_removes_stale_mounts(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {
            f"{DEVICE_PREFIX}inst_old": {"type": "disk", "source": "/host/old", "path": "/inst/old"},
        }

        changed = sync_mounts(instance, [])

        assert changed is True
        assert f"{DEVICE_PREFIX}inst_old" not in instance.devices
        instance.sync.assert_called_once()

    def test_removes_malformed_tracked_device(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {f"{DEVICE_PREFIX}broken": {"type": "disk"}}

        changed = sync_mounts(instance, [])

        assert changed is True
        assert f"{DEVICE_PREFIX}broken" not in instance.devices

    def test_no_change_returns_false(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {
            f"{DEVICE_PREFIX}home_alice": {"type": "disk", "source": "/home/alice", "path": "/home/alice"},
        }

        changed = sync_mounts(instance, [("/home/alice", "/home/alice")])

        assert changed is False
        instance.save.assert_not_called()
        instance.sync.assert_not_called()

    def test_default_instance_path_equals_host_path(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {}
        device_name = _mount_device_name("/home/alice")

        sync_mounts(instance, [("/home/alice", "/home/alice")])

        device = instance.devices[device_name]
        assert device["source"] == "/home/alice"
        assert device["path"] == "/home/alice"

    def test_refuses_to_replace_mount_at_same_path(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {
            f"{DEVICE_PREFIX}old_path": {"type": "disk", "source": "/host/foo", "path": "/shared"},
        }

        with pytest.raises(ValueError, match="Refusing to replace"):
            sync_mounts(instance, [("/host/bar", "/shared")])

        instance.save.assert_not_called()

    def test_rejects_conflicting_desired_paths(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {}

        with pytest.raises(ValueError, match="Conflicting mounts"):
            sync_mounts(instance, [("/host/a", "/same"), ("/host/b", "/same")])

    def test_replace_allows_changing_mount_source(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {
            f"{DEVICE_PREFIX}old": {"type": "disk", "source": "/old", "path": "/shared"},
        }

        changed = sync_mounts(instance, [("/new", "/shared")], replace=True)

        assert changed is True
        assert instance.devices[_mount_device_name("/shared")]["source"] == "/new"
        assert f"{DEVICE_PREFIX}old" not in instance.devices
        instance.save.assert_called_once_with(wait=True)

    def test_replace_overwrites_same_device_name(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {
            _mount_device_name("/shared"): {"type": "disk", "source": "/old", "path": "/shared"},
        }

        changed = sync_mounts(instance, [("/new", "/shared")], replace=True)

        assert changed is True
        assert instance.devices[_mount_device_name("/shared")]["source"] == "/new"

    def test_reuses_existing_device_name_for_unchanged_mount(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {
            f"{DEVICE_PREFIX}home_alice": {"type": "disk", "source": "/home/alice", "path": "/home/alice"},
        }

        sync_mounts(instance, [("/home/alice", "/home/alice"), ("/host/new", "/inst/new")])

        assert instance.devices[f"{DEVICE_PREFIX}home_alice"] == {
            "type": "disk",
            "source": "/home/alice",
            "path": "/home/alice",
        }
        assert instance.devices[_mount_device_name("/inst/new")]["source"] == "/host/new"


class TestGetTrackedMounts:
    def test_returns_tracked_mounts(self) -> None:
        instance = MagicMock()
        instance.devices = {
            f"{DEVICE_PREFIX}home_alice": {"type": "disk", "source": "/home/alice", "path": "/home/alice"},
            f"{DEVICE_PREFIX}mnt_data": {"type": "disk", "source": "/host/data", "path": "/mnt/data"},
        }

        mounts = get_tracked_mounts(instance)

        assert ("/home/alice", "/home/alice") in mounts
        assert ("/host/data", "/mnt/data") in mounts
        assert len(mounts) == 2

    def test_returns_empty_when_no_mounts_tracked(self) -> None:
        instance = MagicMock()
        instance.devices = {}

        assert get_tracked_mounts(instance) == []

    def test_ignores_non_lxcme_devices(self) -> None:
        instance = MagicMock()
        instance.devices = {
            "root": {"type": "disk", "source": "/", "path": "/"},
            f"{DEVICE_PREFIX}data": {"type": "disk", "source": "/data", "path": "/data"},
        }

        mounts = get_tracked_mounts(instance)

        assert len(mounts) == 1
        assert ("/data", "/data") in mounts


class TestSudoersFilename:
    def test_plain_username_unchanged(self) -> None:
        assert _sudoers_filename("alice") == "alice"

    def test_corporate_username_sanitized(self) -> None:
        assert _sudoers_filename("first.last@domain.com") == "first_last_domain_com"

    def test_trailing_tilde_sanitized(self) -> None:
        assert _sudoers_filename("alice~") == "alice_"


class TestSetupPasswordlessSudo:
    def test_writes_sudoers_and_adds_to_group(self) -> None:
        instance = MagicMock()
        instance.execute.return_value = _make_exec_result(exit_code=0)

        setup_passwordless_sudo(instance, "alice")

        instance.files.put.assert_called_once_with(
            "/etc/sudoers.d/alice",
            "alice ALL=(ALL) NOPASSWD:ALL\n",
            mode=0o440,
        )
        calls = [str(c[0][0]) for c in instance.execute.call_args_list]
        assert any("visudo" in c for c in calls)
        assert any("usermod" in c for c in calls)

    def test_raises_on_sudoers_write_failure(self) -> None:
        instance = MagicMock()
        instance.execute.return_value = _make_exec_result(exit_code=0)
        instance.files.put.side_effect = OSError("read-only")

        with pytest.raises(RuntimeError, match="Failed to write sudoers"):
            setup_passwordless_sudo(instance, "alice")

    def test_raises_on_invalid_sudoers(self) -> None:
        instance = MagicMock()
        instance.execute.side_effect = [
            _make_exec_result(exit_code=0),  # mkdir
            _make_exec_result(exit_code=1, stderr="syntax error"),  # visudo
        ]

        with pytest.raises(RuntimeError, match="Invalid sudoers"):
            setup_passwordless_sudo(instance, "alice")

    def test_rejects_unsafe_username(self) -> None:
        instance = MagicMock()

        with pytest.raises(RuntimeError, match="Unsupported username"):
            setup_passwordless_sudo(instance, "alice\nALL=(ALL) NOPASSWD:ALL")

        instance.files.put.assert_not_called()


class TestIsSetupDone:
    def test_returns_true_when_marked(self) -> None:
        instance = MagicMock()
        instance.config = {
            SETUP_DONE_KEY: "true",
            INSTANCE_UID_KEY: "1000",
            INSTANCE_GID_KEY: "1000",
        }
        assert is_setup_done(instance) is True

    def test_returns_false_when_not_marked(self) -> None:
        instance = MagicMock()
        instance.config = {}
        assert is_setup_done(instance) is False

    def test_returns_false_when_uid_gid_missing(self) -> None:
        instance = MagicMock()
        instance.config = {SETUP_DONE_KEY: "true"}
        assert is_setup_done(instance) is False


class TestMarkSetupDone:
    def test_sets_config_keys(self) -> None:
        instance = MagicMock()
        instance.config = {}

        mark_setup_done(instance, instance_uid=1000, instance_gid=1000)

        assert instance.config[SETUP_DONE_KEY] == "true"
        assert instance.config[INSTANCE_UID_KEY] == "1000"
        assert instance.config[INSTANCE_GID_KEY] == "1000"
        instance.save.assert_called_once_with(wait=True)


class TestGetInstanceUserIds:
    def test_returns_stored_ids(self) -> None:
        instance = MagicMock()
        instance.config = {INSTANCE_UID_KEY: "1000", INSTANCE_GID_KEY: "1001"}

        uid, gid = get_instance_user_ids(instance)
        assert uid == 1000
        assert gid == 1001

    def test_raises_on_missing_key(self) -> None:
        instance = MagicMock()
        instance.config = {}

        with pytest.raises(RuntimeError, match="missing stored uid/gid"):
            get_instance_user_ids(instance)


class TestSetupInstanceUser:
    def test_full_flow(self) -> None:
        instance = MagicMock()
        instance.config = {}
        instance.devices = {}
        user = _make_user()

        instance.execute.side_effect = [
            _make_exec_result(stdout="alice:x:1000:\n"),  # getent group
            _make_exec_result(stdout="1000\n"),  # id -u
            _make_exec_result(exit_code=0),  # mkdir sudoers.d
            _make_exec_result(exit_code=0),  # visudo
            _make_exec_result(exit_code=0),  # usermod -aG sudo
        ]

        setup_instance_user(instance, user)

        instance.start.assert_called()
        instance.stop.assert_called_once_with(wait=True)
        assert SETUP_DONE_KEY in instance.config

"""Tests for lxcme.host module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from lxcme.host import (
    _machine_to_lxc_arch,
    _parse_os_release,
    get_host_info,
    instance_alias,
)

OS_RELEASE_UBUNTU = """
ID=ubuntu
VERSION_CODENAME=noble
VERSION_ID=24.04
""".strip()

OS_RELEASE_DEBIAN = """
ID=debian
VERSION_CODENAME=bookworm
VERSION_ID=12
""".strip()

OS_RELEASE_FEDORA = """
ID=fedora
VERSION_ID=40
""".strip()


class TestParseOsRelease:
    def test_parses_quoted_values(self) -> None:
        content = 'ID="ubuntu"\nVERSION_CODENAME="noble"\n'
        with patch("builtins.open", mock_open(read_data=content)):
            result = _parse_os_release()
        assert result["ID"] == "ubuntu"
        assert result["VERSION_CODENAME"] == "noble"

    def test_parses_unquoted_values(self) -> None:
        with patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)):
            result = _parse_os_release()
        assert result["ID"] == "ubuntu"

    def test_skips_comments_and_blanks(self) -> None:
        content = "# comment\n\nID=debian\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = _parse_os_release()
        assert list(result.keys()) == ["ID"]

    def test_raises_on_missing_file(self) -> None:
        with patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                _parse_os_release()


class TestMachineToLxcArch:
    @pytest.mark.parametrize(
        "machine,expected",
        [
            ("x86_64", "amd64"),
            ("aarch64", "arm64"),
            ("armv7l", "armhf"),
            ("ppc64le", "ppc64el"),
            ("s390x", "s390x"),
            ("riscv64", "riscv64"),
            ("unknown_arch", "unknown_arch"),
        ],
    )
    def test_known_and_unknown(self, machine: str, expected: str) -> None:
        assert _machine_to_lxc_arch(machine) == expected


class TestGetHostInfo:
    def _mock_pw(self, name: str = "alice", uid: int = 1000, gid: int = 1000, home: str = "/home/alice") -> MagicMock:
        pw = MagicMock()
        pw.pw_name = name
        pw.pw_uid = uid
        pw.pw_gid = gid
        pw.pw_dir = home
        return pw

    def _mock_gr(self, name: str = "alice", gid: int = 1000) -> MagicMock:
        gr = MagicMock()
        gr.gr_name = name
        gr.gr_gid = gid
        return gr

    def test_detects_ubuntu(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("os.getuid", return_value=1000),
            patch("os.getgid", return_value=1000),
            patch("pwd.getpwuid", return_value=self._mock_pw()),
            patch("grp.getgrgid", return_value=self._mock_gr()),
            patch("platform.machine", return_value="x86_64"),
        ):
            info = get_host_info()

        assert info.distro == "ubuntu"
        assert info.release == "noble"
        assert info.arch == "amd64"
        assert info.user.username == "alice"
        assert info.user.uid == 1000
        assert info.user.home == Path("/home/alice")

    def test_override_distro_release_arch(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("os.getuid", return_value=1000),
            patch("os.getgid", return_value=1000),
            patch("pwd.getpwuid", return_value=self._mock_pw()),
            patch("grp.getgrgid", return_value=self._mock_gr()),
            patch("platform.machine", return_value="x86_64"),
        ):
            info = get_host_info(distro="debian", release="bookworm", arch="arm64")

        assert info.distro == "debian"
        assert info.release == "bookworm"
        assert info.arch == "arm64"

    def test_fedora_falls_back_to_version_id(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_FEDORA)),
            patch("os.getuid", return_value=500),
            patch("os.getgid", return_value=500),
            patch("pwd.getpwuid", return_value=self._mock_pw(uid=500, gid=500)),
            patch("grp.getgrgid", return_value=self._mock_gr(gid=500)),
            patch("platform.machine", return_value="x86_64"),
        ):
            info = get_host_info()

        assert info.distro == "fedora"
        assert info.release == "40"


class TestInstanceAlias:
    def test_triplet_format(self) -> None:
        assert instance_alias("ubuntu", "noble", "amd64") == "ubuntu-noble-amd64"

    def test_custom_values(self) -> None:
        assert instance_alias("debian", "bookworm", "arm64") == "debian-bookworm-arm64"

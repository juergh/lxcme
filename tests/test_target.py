"""Tests for lxcme.target module."""

from __future__ import annotations

from unittest.mock import mock_open, patch

import pytest

from lxcme.target import (
    TargetInfo,
    _get_host_info,
    _machine_to_lxc_arch,
    _parse_os_release,
    get_target_info,
)

OS_RELEASE_UBUNTU = """
ID=ubuntu
VERSION_CODENAME=noble
VERSION_ID=24.04
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

    def test_parses_single_quoted_values(self) -> None:
        content = "ID='ubuntu'\nVERSION_CODENAME='noble'\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = _parse_os_release()
        assert result["ID"] == "ubuntu"
        assert result["VERSION_CODENAME"] == "noble"

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
            ("i686", "i386"),
            ("aarch64", "arm64"),
            ("armv6l", "armhf"),
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
    def test_detects_ubuntu(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            info = _get_host_info()

        assert info[0] == "ubuntu"
        assert info[1] == "noble"
        assert info[2] == "amd64"

    def test_fedora_falls_back_to_version_id(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_FEDORA)),
            patch("platform.machine", return_value="x86_64"),
        ):
            info = _get_host_info()

        assert info[0] == "fedora"
        assert info[1] == "40"


class TestGetTargetInfo:
    def test_defaults_to_host_values(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            target = get_target_info(None, None)

        assert target.distro == "ubuntu"
        assert target.release == "noble"
        assert target.arch == "amd64"

    def test_overrides_distro_and_release(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            target = get_target_info("debian", "bookworm")

        assert target.distro == "debian"
        assert target.release == "bookworm"
        assert target.arch == "amd64"

    def test_overrides_are_lowercased_and_stripped(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            target = get_target_info(" Debian ", " Bookworm ")

        assert target.distro == "debian"
        assert target.release == "bookworm"

    def test_raises_when_release_is_unknown(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data="ID=arch\n")),
            patch("platform.machine", return_value="x86_64"),
        ):
            with pytest.raises(ValueError, match="--release"):
                get_target_info(None, None)


class TestTargetInfo:
    def test_instance_alias_ubuntu_omits_distro(self) -> None:
        target = TargetInfo(distro="ubuntu", release="noble", arch="amd64")
        assert target.instance_alias == "noble"

    def test_instance_alias_debian_omits_distro(self) -> None:
        target = TargetInfo(distro="debian", release="bookworm", arch="amd64")
        assert target.instance_alias == "bookworm"

    def test_instance_alias_other_distro_includes_distro(self) -> None:
        target = TargetInfo(distro="fedora", release="40", arch="amd64")
        assert target.instance_alias == "fedora-40"

    def test_image_alias_includes_distro_and_arch(self) -> None:
        target = TargetInfo(distro="ubuntu", release="noble", arch="amd64")
        assert target.image_alias == "ubuntu-noble-amd64"

    def test_image_alias_debian_includes_distro_and_arch(self) -> None:
        target = TargetInfo(distro="debian", release="bookworm", arch="arm64")
        assert target.image_alias == "debian-bookworm-arm64"

    def test_image_alias_other_distro_includes_distro(self) -> None:
        target = TargetInfo(distro="fedora", release="40", arch="amd64")
        assert target.image_alias == "fedora-40-amd64"

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
    def test_detects_ubuntu(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            info = _get_host_info()

        assert info.distro == "ubuntu"
        assert info.release == "noble"
        assert info.arch == "amd64"

    def test_fedora_falls_back_to_version_id(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_FEDORA)),
            patch("platform.machine", return_value="x86_64"),
        ):
            info = _get_host_info()

        assert info.distro == "fedora"
        assert info.release == "40"


class TestGetTargetInfo:
    def test_defaults_to_host_values(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            target = get_target_info(None, None, None)

        assert target.distro == "ubuntu"
        assert target.release == "noble"
        assert target.arch == "amd64"

    def test_overrides_distro_release_arch(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            target = get_target_info("debian", "bookworm", "arm64")

        assert target.distro == "debian"
        assert target.release == "bookworm"
        assert target.arch == "arm64"

    def test_host_distro_is_always_host(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            target = get_target_info("debian", None, None)

        assert target.host_distro == "ubuntu"

    def test_overrides_are_lowercased(self) -> None:
        with (
            patch("builtins.open", mock_open(read_data=OS_RELEASE_UBUNTU)),
            patch("platform.machine", return_value="x86_64"),
        ):
            target = get_target_info("Debian", "Bookworm", None)

        assert target.distro == "debian"
        assert target.release == "bookworm"


class TestTargetInfo:
    def test_instance_alias_ubuntu_omits_distro(self) -> None:
        target = TargetInfo(distro="ubuntu", release="noble", arch="amd64", host_distro="ubuntu")
        assert target.instance_alias == "noble-amd64"

    def test_instance_alias_debian_omits_distro(self) -> None:
        target = TargetInfo(distro="debian", release="bookworm", arch="amd64", host_distro="ubuntu")
        assert target.instance_alias == "bookworm-amd64"

    def test_instance_alias_other_distro_includes_distro(self) -> None:
        target = TargetInfo(distro="fedora", release="40", arch="amd64", host_distro="ubuntu")
        assert target.instance_alias == "fedora-40-amd64"

    def test_image_alias_ubuntu_omits_distro(self) -> None:
        target = TargetInfo(distro="ubuntu", release="noble", arch="amd64", host_distro="ubuntu")
        assert target.image_alias == "noble-amd64"

    def test_image_alias_debian_omits_distro(self) -> None:
        target = TargetInfo(distro="debian", release="bookworm", arch="arm64", host_distro="ubuntu")
        assert target.image_alias == "bookworm-arm64"

    def test_image_alias_other_distro_includes_distro(self) -> None:
        target = TargetInfo(distro="fedora", release="40", arch="amd64", host_distro="ubuntu")
        assert target.image_alias == "fedora-40-amd64"

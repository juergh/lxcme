"""Host system introspection: distro, release, architecture."""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class HostInfo:
    """Host system information as detected from /etc/os-release and platform."""

    distro: str
    release: str
    arch: str


@dataclass(frozen=True)
class TargetInfo:
    """Target instance configuration, derived from host info with optional overrides.

    Provides :attr:`instance_alias` and :attr:`image_alias` as the canonical
    names for the LXC instance and its source image respectively.
    """

    distro: str
    release: str
    arch: str
    host_distro: str

    @property
    def instance_alias(self) -> str:
        """Instance name: ``release-arch`` when distro matches host, else ``distro-release-arch``."""
        if self.distro == self.host_distro:
            return f"{self.release}-{self.arch}"
        return f"{self.distro}-{self.release}-{self.arch}"

    @property
    def image_alias(self) -> str:
        """Image alias: always the full ``distro-release-arch`` triple."""
        return f"{self.distro}-{self.release}-{self.arch}"


def _parse_os_release() -> dict[str, str]:
    """Parse /etc/os-release into a key/value dict."""
    result: dict[str, str] = {}
    with open("/etc/os-release") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"')
    return result


def get_host_info() -> HostInfo:
    """Detect host system information from /etc/os-release and platform."""
    os_release = _parse_os_release()
    return HostInfo(
        distro=os_release.get("ID", "").lower(),
        release=(os_release.get("VERSION_CODENAME") or os_release.get("VERSION_ID", "")).lower(),
        arch=_machine_to_lxc_arch(platform.machine()),
    )


def get_target_info(
    host: HostInfo,
    distro: str | None = None,
    release: str | None = None,
    arch: str | None = None,
) -> TargetInfo:
    """Build a TargetInfo from host info with optional distro/release/arch overrides."""
    return TargetInfo(
        distro=(distro or host.distro).lower(),
        release=(release or host.release).lower(),
        arch=arch or host.arch,
        host_distro=host.distro,
    )


def _machine_to_lxc_arch(machine: str) -> str:
    """Translate platform.machine() output to LXC architecture alias."""
    mapping = {
        "x86_64": "amd64",
        "aarch64": "arm64",
        "armv7l": "armhf",
        "ppc64le": "ppc64el",
        "s390x": "s390x",
        "riscv64": "riscv64",
    }
    return mapping.get(machine, machine)

"""Target instance configuration with host system introspection."""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class _HostInfo:
    """Host system information from /etc/os-release and platform."""

    distro: str
    release: str
    arch: str


@dataclass(frozen=True)
class TargetInfo:
    """Target instance configuration with defaults derived from host info."""

    distro: str
    release: str
    arch: str
    host_distro: str

    @property
    def instance_alias(self) -> str:
        """Instance name (release-arch for Ubuntu/Debian, else distro-release-arch)."""
        if self.distro in ("ubuntu", "debian"):
            return f"{self.release}-{self.arch}"
        return f"{self.distro}-{self.release}-{self.arch}"

    @property
    def image_alias(self) -> str:
        """Image alias (distro-release-arch for all distros)."""
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


def _get_host_info() -> _HostInfo:
    """Detect host system information from /etc/os-release and platform."""
    os_release = _parse_os_release()
    return _HostInfo(
        distro=os_release.get("ID", "").lower(),
        release=(os_release.get("VERSION_CODENAME") or os_release.get("VERSION_ID", "")).lower(),
        arch=_machine_to_lxc_arch(platform.machine()),
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


def get_target_info(
    distro: str | None,
    release: str | None,
    arch: str | None,
) -> TargetInfo:
    """Build TargetInfo with optional overrides, using host defaults for unspecified values."""
    host = _get_host_info()
    return TargetInfo(
        distro=(distro or host.distro).lower(),
        release=(release or host.release).lower(),
        arch=arch or host.arch,
        host_distro=host.distro,
    )

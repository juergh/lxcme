"""Host system introspection: distro, release, architecture."""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class HostInfo:
    """Host system information used to derive LXC image aliases and instance names."""

    distro: str
    release: str
    arch: str


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


def get_host_info(
    distro: str | None = None,
    release: str | None = None,
    arch: str | None = None,
) -> HostInfo:
    """Detect host system information, with optional overrides."""
    os_release = _parse_os_release()

    resolved_distro = (distro or os_release.get("ID", "")).lower()
    resolved_release = (release or os_release.get("VERSION_CODENAME") or os_release.get("VERSION_ID", "")).lower()
    resolved_arch = arch or _machine_to_lxc_arch(platform.machine())

    return HostInfo(
        distro=resolved_distro,
        release=resolved_release,
        arch=resolved_arch,
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


def instance_alias(distro: str, release: str, arch: str) -> str:
    """Return the canonical LXC image alias / instance name triplet."""
    return f"{distro}-{release}-{arch}"

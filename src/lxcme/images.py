"""LXC image management: remote selection, local lookup, and download."""

from __future__ import annotations

import logging

import pylxd

logger = logging.getLogger(__name__)

UBUNTU_REMOTE = "https://cloud-images.ubuntu.com/daily"
DEFAULT_REMOTE = "https://images.linuxcontainers.org"


def get_remote(distro: str) -> str:
    """Return the appropriate simplestreams remote URL for the given distribution."""
    return UBUNTU_REMOTE if distro.lower() == "ubuntu" else DEFAULT_REMOTE


def get_remote_alias(distro: str, release: str) -> str:
    """Return the simplestreams alias used to look up an image on the remote.

    Ubuntu daily uses bare codenames (e.g. ``resolute``).
    The linuxcontainers.org remote uses ``distro/release`` (e.g. ``debian/bookworm``).
    In both cases the LXD daemon resolves the host architecture automatically.
    """
    if distro.lower() == "ubuntu":
        return release
    return f"{distro}/{release}"


def find_local_image(client: pylxd.Client, alias: str) -> pylxd.models.Image | None:
    """Search for a locally cached LXC image by alias."""
    for image in client.images.all():
        for img_alias in image.aliases:
            if img_alias.get("name") == alias:
                return image
    return None


def ensure_image(
    client: pylxd.Client, distro: str, release: str, local_alias: str
) -> pylxd.models.Image:
    """Ensure a local LXC image exists, downloading from simplestreams if necessary.

    Uses ``local_alias`` for local cache lookups and display. Derives the correct
    simplestreams alias (e.g. ``resolute`` or ``debian/bookworm``) automatically.

    Args:
        client: Active pylxd client.
        distro: Distribution name (e.g. ``ubuntu``, ``debian``).
        release: Release codename or version (e.g. ``resolute``, ``bookworm``).
        local_alias: Full local alias used for cache lookup (e.g. ``ubuntu-resolute-amd64``).

    Returns:
        The local pylxd Image object, downloaded if it was not already cached.
    """
    image = find_local_image(client, local_alias)
    if image is not None:
        logger.info("Using cached local image: %s", local_alias)
        return image

    remote = get_remote(distro)
    remote_alias = get_remote_alias(distro, release)
    logger.info("Downloading image '%s' from remote '%s'...", local_alias, remote)

    image = client.images.create_from_simplestreams(remote, remote_alias)
    logger.info("Image '%s' downloaded successfully.", local_alias)
    return image

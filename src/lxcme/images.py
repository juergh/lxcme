"""LXC image management: remote selection, local lookup, and download."""

from __future__ import annotations

import logging

import pylxd

logger = logging.getLogger(__name__)

UBUNTU_REMOTE = "https://cloud-images.ubuntu.com/daily"
DEFAULT_REMOTE = "https://images.lxd.canonical.com"


def get_remote(distro: str) -> str:
    """Return simplestreams remote URL for given distribution."""
    return UBUNTU_REMOTE if distro.lower() == "ubuntu" else DEFAULT_REMOTE


def get_remote_alias(distro: str, release: str) -> str:
    """Return simplestreams alias for given distro and release."""
    if distro.lower() == "ubuntu":
        return release
    return f"{distro}/{release}"


def find_local_image(client: pylxd.Client, alias: str) -> pylxd.models.Image | None:
    """Search local LXD image store for image matching given alias."""
    for image in client.images.all():
        for img_alias in image.aliases:
            if img_alias.get("name") == alias:
                return image
    return None


def ensure_image(client: pylxd.Client, distro: str, release: str, local_alias: str) -> pylxd.models.Image:
    """Ensure local LXC image exists, downloading from simplestreams if necessary."""
    image = find_local_image(client, local_alias)
    if image is not None:
        logger.info("Using cached local image: %s", local_alias)
        return image

    remote = get_remote(distro)
    remote_alias = get_remote_alias(distro, release)
    logger.info("Downloading image '%s' from remote '%s'...", local_alias, remote)

    image = client.images.create_from_simplestreams(remote, remote_alias)
    image.add_alias(local_alias, "")
    logger.info("Image '%s' downloaded successfully.", local_alias)
    return image

"""LXC image management: remote selection, local lookup, and download."""

from __future__ import annotations

import logging

import pylxd

logger = logging.getLogger(__name__)

# Remote used for Ubuntu images (daily builds)
UBUNTU_REMOTE = "ubuntu-daily"
# Remote used for all other distributions
DEFAULT_REMOTE = "images"


def get_remote(distro: str) -> str:
    """Return the appropriate LXC remote for the given distribution."""
    return UBUNTU_REMOTE if distro.lower() == "ubuntu" else DEFAULT_REMOTE


def find_local_image(client: pylxd.Client, alias: str) -> pylxd.models.Image | None:
    """Search for a locally cached LXC image by alias."""
    for image in client.images.all():
        for img_alias in image.aliases:
            if img_alias.get("name") == alias:
                return image
    return None


def ensure_image(client: pylxd.Client, distro: str, alias: str) -> pylxd.models.Image:
    """Ensure a local LXC image exists for the given alias, downloading if necessary.

    Checks the local image store first. If not found, downloads from the
    appropriate remote (ubuntu-daily for Ubuntu, images: for others).
    """
    image = find_local_image(client, alias)
    if image is not None:
        logger.info("Using cached local image: %s", alias)
        return image

    remote = get_remote(distro)
    logger.info("Downloading image '%s' from remote '%s'...", alias, remote)

    image = client.images.create_from_simplestreams(remote, alias)
    logger.info("Image '%s' downloaded successfully.", alias)
    return image


def image_alias(distro: str, release: str, arch: str) -> str:
    """Return the canonical LXC image alias derived from distro, release, and arch."""
    return f"{distro}-{release}-{arch}"

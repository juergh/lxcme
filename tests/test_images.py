"""Tests for lxcme.images module."""

from __future__ import annotations

from unittest.mock import MagicMock

from lxcme.images import DEFAULT_REMOTE, UBUNTU_REMOTE, ensure_image, find_local_image, get_remote


def _make_image(aliases: list[str]) -> MagicMock:
    image = MagicMock()
    image.aliases = [{"name": a} for a in aliases]
    image.fingerprint = "abc123"
    return image


class TestGetRemote:
    def test_ubuntu_returns_ubuntu_daily(self) -> None:
        assert get_remote("ubuntu") == UBUNTU_REMOTE

    def test_ubuntu_case_insensitive(self) -> None:
        assert get_remote("Ubuntu") == UBUNTU_REMOTE

    def test_debian_returns_default(self) -> None:
        assert get_remote("debian") == DEFAULT_REMOTE

    def test_fedora_returns_default(self) -> None:
        assert get_remote("fedora") == DEFAULT_REMOTE


class TestFindLocalImage:
    def test_finds_matching_alias(self) -> None:
        client = MagicMock()
        img = _make_image(["ubuntu-noble-amd64", "ubuntu/noble/amd64"])
        client.images.all.return_value = [img]

        result = find_local_image(client, "ubuntu-noble-amd64")
        assert result is img

    def test_returns_none_when_not_found(self) -> None:
        client = MagicMock()
        img = _make_image(["debian-bookworm-amd64"])
        client.images.all.return_value = [img]

        result = find_local_image(client, "ubuntu-noble-amd64")
        assert result is None

    def test_returns_none_on_empty_store(self) -> None:
        client = MagicMock()
        client.images.all.return_value = []
        assert find_local_image(client, "ubuntu-noble-amd64") is None


class TestEnsureImage:
    def test_returns_cached_image_without_download(self) -> None:
        client = MagicMock()
        img = _make_image(["ubuntu-noble-amd64"])
        client.images.all.return_value = [img]

        result = ensure_image(client, "ubuntu", "ubuntu-noble-amd64")
        assert result is img
        client.images.create_from_simplestreams.assert_not_called()

    def test_downloads_when_not_cached(self) -> None:
        client = MagicMock()
        client.images.all.return_value = []
        downloaded = _make_image(["ubuntu-noble-amd64"])
        client.images.create_from_simplestreams.return_value = downloaded

        result = ensure_image(client, "ubuntu", "ubuntu-noble-amd64")
        assert result is downloaded
        client.images.create_from_simplestreams.assert_called_once_with(UBUNTU_REMOTE, "ubuntu-noble-amd64")

    def test_uses_images_remote_for_non_ubuntu(self) -> None:
        client = MagicMock()
        client.images.all.return_value = []
        client.images.create_from_simplestreams.return_value = _make_image(["debian-bookworm-amd64"])

        ensure_image(client, "debian", "debian-bookworm-amd64")
        client.images.create_from_simplestreams.assert_called_once_with(DEFAULT_REMOTE, "debian-bookworm-amd64")

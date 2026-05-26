# lxcme

A CLI tool to manage LXC containers with seamless user and home directory integration.

`lxcme` launches and enters LXC containers that feel like your local machine: your user, your home directory, your uid/gid — all mapped correctly into the container.

## Requirements

- LXD installed and running
- `lxc` CLI available on `$PATH`
- Python 3.11+

## Installation

```bash
uv tool install lxcme
```

Or from source:

```bash
git clone ...
cd lxcme
uv tool install .
```

## Usage

```
lxcme [options] [instance_name] [[--] command [args...]]
```

### Options

| Option | Description |
|---|---|
| `--distro DISTRO` | Override host distribution name |
| `--release RELEASE` | Override host release name |
| `--arch ARCH` | Override host architecture |
| `--root` | Run command as root inside the instance |
| `--no-home` | Create an empty home dir inside the instance instead of mounting the host home |
| `-v / --verbose` | Enable debug logging |

### Examples

```bash
# Enter default instance (distro/release/arch detected from host)
lxcme

# Enter a named instance
lxcme my-box

# Run a non-interactive command
lxcme my-box -- python3 script.py

# Run as root
lxcme --root -- apt update

# Use a specific distro/release
lxcme --distro debian --release bookworm

# Keep home inside the container (don't mount host home)
lxcme --no-home
```

## How it works

### Instance naming

Distro, release, and arch are auto-detected from the host via `/etc/os-release` and `platform.machine()`. If no instance name is given, one is derived automatically:

- **Same distro as host**: `<release>-<arch>` (e.g. `noble-amd64`)
- **Different distro**: `<distro>-<release>-<arch>` (e.g. `debian-bookworm-amd64`)

The image alias used for lookup and download is always the full `<distro>-<release>-<arch>` triple regardless of the host distro.

### Image remotes

| Distro | Remote |
|---|---|
| Ubuntu | `ubuntu-daily:` |
| Everything else | `images:` |

If the image is not cached locally it is downloaded automatically before the container is created.

### First-launch setup

The first time an instance is started, `lxcme` provisions it:

1. Ensures the current user and group exist inside the container (creates them if absent).
2. Creates an empty home directory inside the container (only with `--no-home`).
3. Grants the user passwordless `sudo` via `/etc/sudoers.d/<user>`.
4. Writes `raw.idmap` config to map the host uid/gid to the uid/gid the user has inside the container.
5. Attaches the host home directory as a disk device at the same path (skipped with `--no-home`).
6. Stores the container-side uid/gid in the instance config.
7. Restarts the container to apply the idmap and disk device config.

Setup runs exactly once and is idempotent — tracked via the `user.lxcme.setup-done` instance config key.

### Command execution

Commands are run as the current user inside the container:

```
lxc exec <instance> --user <uid> --group <gid> --cwd <home> -- <command>
```

The uid/gid used are the ones the user has *inside* the container (stored at first launch), not the host uid/gid. This ensures correct behaviour even when the two differ.

- **Interactive** commands (shells: `bash`, `sh`, `zsh`, `fish`, etc.) use `os.execvp` to replace the current process, giving a proper TTY and correct signal handling.
- **Non-interactive** commands use `pylxd`'s `execute()`.

Pass `--root` to run as root instead.

## Project layout

```
src/lxcme/
├── cli.py        # click entrypoint
├── host.py       # host detection (HostInfo) and target configuration (TargetInfo)
├── images.py     # local image lookup and remote download
├── instances.py  # container lifecycle and exec
└── users.py      # first-launch provisioning
tests/
├── test_cli.py
├── test_host.py
├── test_images.py
├── test_instances.py
└── test_users.py
```

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type checking
uv run mypy src/lxcme/
```

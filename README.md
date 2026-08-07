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
| `--mount HOST[:INST]` | Add a directory mount. Repeatable. If `INST` is omitted, `HOST` is used as the instance path. |
| `--clear-mounts` | Remove all existing mounts before adding new ones. |
| `--env KEY=VALUE` | Set an environment variable inside the instance. Repeatable. |
| `--cwd PATH` | Set the working directory inside the instance (default: user home dir; `/` when `--root` is used without `--cwd`). |
| `--wait` | Wait for interactive session to exit instead of replacing process. Useful for wrapper scripts. |
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

# Add /home/alice mount
lxcme --mount /home/alice

# Mount a project directory at a custom path inside the instance
lxcme --mount /host/projects:/work

# Add multiple mounts
lxcme --mount /home/alice --mount /data:/mnt/data

# Remove all mounts
lxcme --clear-mounts

# Replace all mounts with /data
lxcme --clear-mounts --mount /data

# Use a specific distro/release
lxcme --distro debian --release bookworm

# Pass environment variables into the instance
lxcme --env FOO=bar --env BAZ=qux -- printenv FOO

# Set working directory inside the instance
lxcme --cwd /opt/myapp -- bash
lxcme --root --cwd /etc -- ls
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
2. Grants the user passwordless `sudo` via `/etc/sudoers.d/<user>`.
3. Writes `raw.idmap` config to map the host uid/gid to the uid/gid the user has inside the container.
4. Stores the container-side uid/gid in the instance config.
5. Restarts the container to apply the idmap config.

Setup runs exactly once and is idempotent — tracked via the `user.lxcme.setup-done` instance config key.

### Mounts

Directory mounts are managed via `--mount HOST[:INST]`. The `--mount` option is repeatable and always **additive** — each invocation adds a mount if it is not already present. If a mount is already tracked, a warning is printed and no duplicate is created.

Use `--clear-mounts` to remove all existing mounts before applying any `--mount` flags.

If `INST` is omitted, `HOST` is used as the path inside the instance.

```bash
# Add /foo mount
lxcme --mount /foo

# Add /bar with a custom instance path
lxcme --mount /bar:/opt/bar

# Replace all mounts with /foo
lxcme --clear-mounts --mount /foo

# Remove all mounts
lxcme --clear-mounts
```

Mounts are tracked as LXD disk devices with a `lxcme_` prefix (derived from the instance path). This means existing mounts are discoverable via standard `lxc config device show` output.

### Command execution

Commands are run as the current user inside the container:

```
lxc exec <instance> --cwd <cwd> --user <uid> --group <gid> -- <command>
```

The working directory defaults to the user's home directory inside the instance, or `/` for root. Use `--cwd PATH` to override for any invocation regardless of `--root`.

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


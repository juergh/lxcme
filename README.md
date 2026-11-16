# lxcme

A CLI tool to manage LXD containers with seamless user and optional home directory integration.

`lxcme` launches and enters LXD containers that feel like your local machine: your user, your uid/gid, and optionally your home directory are mapped into the container.

## Requirements

- LXD installed and running
- `lxc` CLI available on `$PATH`
- Python 3.12+

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
lxcme [options] [instance_name] [--] [command [args...]]
```

Without `--`, the first positional argument is the instance name and the remaining arguments are the command. Use `--` before the command whenever it takes options of its own, for example `lxcme -- ls -la` or `lxcme my-box -- python3 script.py`.

### Options

| Option | Description |
|---|---|
| `--distro DISTRO` | Override host distribution name |
| `--release RELEASE` | Override host release name |
| `--root` | Run command as root inside the instance |
| `--mount HOST[:INST]` | Add a directory mount. Repeatable. If `INST` is omitted, `HOST` is used as the instance path. |
| `--home HOST` | Mount `HOST` at the user's home directory inside the instance. |
| `--work` | Mount the current directory at `/work-<hash>` and use it as the working directory. |
| `--clear-mounts` | Remove all lxcme-managed mounts before adding new ones. Affects every process using the instance. |
| `--prune-work` | Remove `--work` mounts no process in the instance is using. |
| `--mount-plan` | Show the mount changes a run would apply and exit without applying them. Requires an existing instance. |
| `--env KEY=VALUE` | Set an environment variable inside the instance. Repeatable. |
| `--cwd PATH` | Set the working directory inside the instance. |
| `--wait` | Wait for interactive session to exit instead of replacing the process. Useful for wrapper scripts. |
| `--version` | Show the version and exit. |
| `-v / --verbose` | Enable debug logging |

### Examples

```bash
# Enter default instance (distro/release detected from host)
lxcme

# Enter a named instance
lxcme my-box

# Run a non-interactive command
lxcme my-box -- python3 script.py
lxcme -- ls -la

# Run as root
lxcme --root -- apt update

# Mount the host home directory as $HOME
lxcme --home /home/alice

# Mount a project directory at a custom path inside the instance
lxcme --mount /host/projects:/work

# Mount the current directory and enter it
lxcme --work my-box

# Add multiple mounts
lxcme --mount /home/alice --mount /data:/mnt/data

# Remove all lxcme-managed mounts
lxcme --clear-mounts

# Replace all mounts with /data
lxcme --clear-mounts --mount /data

# Remove unused work mounts
lxcme --prune-work

# Remove unused work mounts and enter the current directory
lxcme --prune-work --work

# Show what mounting /data would change, then exit without applying it
lxcme --mount-plan --mount /data

# Show what clearing all mounts would change
lxcme --mount-plan --clear-mounts

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

Distro, release, and architecture are auto-detected from the host via `/etc/os-release` and `platform.machine()`. If no instance name is given, one is derived automatically:

- **Ubuntu/Debian**: `<release>` (e.g. `noble`, `bookworm`)
- **Other distros**: `<distro>-<release>` (e.g. `fedora-40`)

The architecture is not part of the instance name. It is part of the local image alias, which is always `<distro>-<release>-<arch>`.

### Image remotes

| Distro | Remote |
|---|---|
| Ubuntu | `https://cloud-images.ubuntu.com/daily` |
| Everything else | `https://images.lxd.canonical.com` |

If the image is not cached locally it is downloaded automatically before the container is created.

### First-launch setup

The first time an instance is started, `lxcme` provisions it:

1. Ensures the current user and group exist inside the container (creates them if absent).
2. Grants the user passwordless `sudo` via `/etc/sudoers.d/<user>`.
3. Writes `raw.idmap` config to map the host uid/gid to the uid/gid the user has inside the container.
4. Stores the container-side uid/gid in the instance config.
5. Restarts the container to apply the idmap config.

No home directory is created. Use `--home` or `--mount` to provide one. Without a home mount, `HOME` and the default working directory are `/`.

Setup runs exactly once and is idempotent — tracked via the `user.lxcme.setup-done` instance config key. lxcme refuses to modify containers it did not create, identified by the `user.lxcme.managed` config key.

### Mounts

Directory mounts are managed via `--mount HOST[:INST]`. The `--mount` option is repeatable and **additive**: each invocation adds a mount if it is not already present. If the exact mount is already tracked, a warning is printed and no duplicate is created.

Mounts are instance-global. Replacing a mount affects every process using the container, so lxcme never replaces a mount implicitly. If a different host directory is already mounted at the requested instance path, lxcme exits with an error. Use `--clear-mounts` to explicitly replace mounts.

`--home HOST` is shorthand for mounting `HOST` at the user's home directory. It is singular:

```bash
# Add a home mount
lxcme --home /home/alice

# Replace all mounts with a new home
lxcme --clear-mounts --home /home/alice
```

Without a home mount, `HOME` and the default working directory are `/`. If a home mount exists at the user's home path, `HOME` and the default working directory use it.

`--work` mounts the current directory at `/work-<hash>` and uses that as the working directory unless `--cwd` is given.

If `INST` is omitted, `HOST` is used as the path inside the instance. Host paths must exist and be directories; instance paths must be absolute. Host paths support `~` expansion and are canonicalized (symlinks resolved) before use, so `--mount`, `--home`, and `--work` all refer to the same directory regardless of how it is spelled.

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

Mounts are tracked as LXD disk devices with a `lxcme_` prefix. New device names include a short hash of the instance path to avoid collisions; existing device names are reused.

`--clear-mounts` is destructive and instance-global. It affects every process using the container and does not prompt.

`--prune-work` removes `--work` mounts that no process in the instance is using. Work mounts are identified from the instance's LXD device config by matching the instance path to the hash of the host source. A mount is considered in use when a process has it as its working directory, root, executable, or an open file descriptor. The current `--work` and `--cwd` targets are never pruned. Pruning only runs when the instance is already running; otherwise lxcme warns and skips it. Detection is best-effort: a process can enter a mount between the check and removal. `--clear-mounts` takes precedence when both flags are given.

`--mount-plan` shows the changes a run would apply to the instance's mounts and exits without applying them. It requires an existing instance and never creates, starts, provisions, or enters the container. Output marks mounts that would be added (`+`), removed (`-`), and left unchanged (`=`). Pruning is previewed only when the instance is already running; otherwise the plan reports that pruning is skipped.

### Command execution

Commands are run as the current user inside the container:

```
lxc exec <instance> --cwd <cwd> --user <uid> --group <gid> -- <command>
```

The working directory defaults to the user's home directory when a home mount exists, otherwise `/`. Root always defaults to `/`. Use `--cwd PATH` to override for any invocation regardless of `--root`.

- **Interactive** commands (shells: `bash`, `sh`, `zsh`, `fish`, etc.) use `os.execvp` to replace the current process, giving a proper TTY and correct signal handling.
- **Non-interactive** commands use `pylxd`'s `execute()`.

Pass `--root` to run as root instead. Non-interactive root commands run with `HOME=/root`, `USER=root`, and `LOGNAME=root`; `--home` does not change root's `HOME`. Use `--env` to override any of these.

### Security notes

- Mounts expose host directories to the container. Only mount directories you trust the container with.
- The provisioned user gets passwordless `sudo` inside the container.
- On a shared LXD host, any user in the `lxd` group can access all containers. Use distinct instance names and do not share containers between users.

## Project layout

```
src/lxcme/
├── cli.py        # click entrypoint
├── target.py     # host detection and target configuration (TargetInfo)
├── images.py     # local image lookup and remote download
├── instances.py  # container lifecycle and exec
└── users.py      # first-launch provisioning and mount reconciliation
tests/
├── test_cli.py
├── test_images.py
├── test_instances.py
├── test_target.py
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

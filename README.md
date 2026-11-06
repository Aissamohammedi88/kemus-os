# KEMUS OS

> **The Declaration of War against the Silicon Valley Monoliths.**  
> **Building is child’s play. “Developer” is an insult. “Systems Creator” is the future.**  
> *We don’t just run code. We execute missions.*

Kemus is a lightweight, browser-rendered **virtual desktop** served by a single Python application. It brings a desktop-style workspace, virtual terminal, persistent file manager, text editor, calculator, settings, and help into one local web interface—with no third-party Python runtime dependencies.

**This is not a repo. It’s a warning.** A challenge to the assumption that useful systems must be built and controlled by a handful of technology giants. Build boldly. Keep tools inspectable. Give creators the keys.

> **What Kemus is—and is not:** Kemus is a local web application that simulates a desktop environment. It is not a Linux distribution, kernel, virtual machine, container, or security boundary. Several system commands and package-manager responses are simulated.

## What’s in the repository

The release files currently live at the repository root:

| File | Purpose |
|---|---|
| `kemus.py` | Python standard-library HTTP server, embedded HTML/CSS/JavaScript interface, virtual filesystem, terminal commands, and JSON API |
| `pyproject.toml` | Python package metadata and the `kemus` command entry point |
| `install(1).sh` | Bash installer/launcher with automatic, user, development, virtual-environment, and zero-install fallback paths |
| `lance-avec- ceci.sh` | Additional launch/install helper included in the repository |
| `kemus-1.0.tar.gz` | Packaged source archive |

The runtime stores the virtual computer’s files and its log in `~/.kemus/` (specifically `~/.kemus/home/` and `~/.kemus/kemus.log`); these are outside the source checkout.

## Features

- Desktop-style UI with draggable, resizable, minimizable, maximizable, and closable windows.
- App launcher, task list, dock, desktop shortcuts, and clock.
- Virtual file manager with seeded folders and files, plus a text editor that reads and saves files in the virtual filesystem.
- Terminal with commands such as `ls`, `cd`, `pwd`, `cat`, `echo`, `mkdir`, `touch`, `rm`, `mv`, `cp`, `find`, `tree`, `date`, `whoami`, `uname`, `neofetch`, `ps`, `top`, `df`, and `free`.
- Python command support (`python` / `python3`) and a calculator.
- JSON endpoints for health, desktop metadata, virtual files, terminal commands, and saving files.
- Python 3.8+ runtime; the application itself uses Python’s standard library.

Some UI elements are intentionally simulated: the browser app is a mock, package-manager commands do not install real system packages, and system/process/resource information is illustrative rather than a live view of the host.

## Quick start

Requirements: Python 3.8 or newer.

Run directly from the repository root:

```bash
python3 kemus.py
```

Then open [http://127.0.0.1:3922](http://127.0.0.1:3922). Stop the server with `Ctrl+C`.

Choose a different port with an environment variable:

```bash
KEMUS_PORT=4000 python3 kemus.py
```

The tracked installer can also install and launch Kemus:

```bash
bash 'install(1).sh'
```

See the installer’s `--help` output for supported options. For a development install, use a virtual environment and then run `python3 -m pip install -e .` from the repository root. Direct execution (`python3 kemus.py`) does not require pip.

## Data and API

Kemus seeds a starter workspace on first launch under `~/.kemus/home/`. The interface communicates with the local server through these endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Health and version information |
| `GET` | `/api/desktop` | Virtual desktop metadata |
| `GET` | `/api/fs?path=...` | List a directory inside the virtual filesystem |
| `GET` | `/api/file?path=...` | Read a virtual file |
| `POST` | `/api/terminal` | Run a supported terminal command |
| `POST` | `/api/file/save` | Save text to the virtual filesystem |

## Security: read before running

Kemus is an experimental local application, **not hardened for multi-user or untrusted-network use**. By default, the server binds to `127.0.0.1`; do not expose it to a network or set `KEMUS_HOST` to a public interface without adding and reviewing appropriate authentication and security controls.

The terminal’s `python` command launches Python code as the same operating-system user running Kemus. Setting the subprocess working directory to the virtual filesystem does **not** sandbox that code. The HTTP API also has no authentication in this version, and its file-save and terminal endpoints can modify the virtual filesystem. Only run Kemus in an environment you control, and do not treat its virtual filesystem as an OS-level isolation boundary.

Please report security issues privately through GitHub’s repository security reporting feature rather than posting sensitive details in a public issue. See [SECURITY.md](SECURITY.md) if present.

## Project status

Kemus 1.0 is an experimental proof of concept. The source and archive are present; documentation describes the behavior implemented in the checked-in source, including explicit notes where features are simulated. No claim is made here that it is a production operating system or a secure sandbox.

## Contributing

Issues and pull requests are welcome. Include reproduction steps for bugs, describe the expected behavior, and keep contributions lawful and respectful. Test changes only on systems you own or are authorized to use.

## Project identity

- **Project:** KEMUS OS / Kemus
- **Creator:** Aissa Mohammedi (DGK)
- **Repository:** [`Aissamohammedi88/kemus-os`](https://github.com/Aissamohammedi88/kemus-os)
- **Tagline:** *We don’t just run code. We execute missions.*

---

**Build systems. Keep them inspectable. Give creators the keys.**

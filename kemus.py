#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kemus — PC virtuel leger, inspire KDE Plasma / NixOS / Fedora.
Serveur HTTP Python stdlib UNIQUEMENT (zero dependance pip).
Port par defaut : 3922 (permanent).

Lancement :
    python3 kemus.py                  # http://localhost:3922
    KEMUS_PORT=4000 python3 kemus.py  # port custom
    kemus                             # apres pip install kemus

Tous les fichiers du "PC" vivent dans ~/.kemus/home/ (VFS reel persistant).
"""
import os
import sys
import json
import time
import shutil
import socket
import hashlib
import secrets
import subprocess
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote
from pathlib import Path

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
KEMUS_HOME = Path.home() / ".kemus"
KEMUS_HOME.mkdir(parents=True, exist_ok=True)
VFS_ROOT = KEMUS_HOME / "home"
VFS_ROOT.mkdir(parents=True, exist_ok=True)
LOG_FILE = KEMUS_HOME / "kemus.log"
PORT = int(os.environ.get("KEMUS_PORT", "3922"))
HOST = os.environ.get("KEMUS_HOST", "127.0.0.1")
VERSION = "1.0.0"
DISTRO = "KemusOS"

# Sessions: token -> {"cwd": str, "history": [str]}
SESSIONS: dict[str, dict] = {}

# ----------------------------------------------------------------------------
# Seed filesystem (first boot)
# ----------------------------------------------------------------------------
def seed_vfs():
    files = {
        "README.txt": (
            "Bienvenue dans Kemus - PC virtuel leger.\n"
            "Inspire de KDE Plasma, NixOS et Fedora.\n\n"
            "Terminal : tapez 'help' pour la liste des commandes.\n"
            "Tentez aussi : neofetch, ls, cat hello.py\n"
        ),
        "hello.py": 'print("Hello from Kemus!")\n',
        "Documents/notes.md": (
            "# Mes notes\n\n"
            "- Essayer neofetch dans le terminal\n"
            "- Explorer le menu Kickoff (icone flocon)\n"
            "- Fenetres drag & drop, redimensionnables\n"
        ),
        "Documents/welcome.txt": "Fichier de bienvenue - editez-moi !\n",
        "Projects/hello.py": 'print("Project 1")\n',
        "Pictures/.keep": "",
        "Music/.keep": "",
        "Downloads/.keep": "",
    }
    for rel, content in files.items():
        p = VFS_ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(content, encoding="utf-8")


# ----------------------------------------------------------------------------
# Security: resolve and sandbox a path under VFS_ROOT
# ----------------------------------------------------------------------------
def safe_path(rel: str, cwd: str) -> Path:
    """Resolve rel against cwd, then ensure final path is inside VFS_ROOT."""
    if rel is None or rel == "":
        rel = "."
    # Build absolute virtual path
    if rel.startswith("/"):
        virtual = rel.lstrip("/")
    else:
        virtual = os.path.normpath(os.path.join(cwd, rel))
    virtual = virtual.lstrip("/")
    target = (VFS_ROOT / virtual).resolve()
    try:
        target.relative_to(VFS_ROOT.resolve())
    except ValueError:
        raise PermissionError(f"Acces refuse (hors sandbox) : {rel}")
    return target


def rel_from_root(p: Path) -> str:
    try:
        rel = p.relative_to(VFS_ROOT.resolve())
    except ValueError:
        return "/"
    s = str(rel)
    return "/" + s if s != "." else "/"


# ----------------------------------------------------------------------------
# Terminal command interpreter (returns text, updates cwd)
# ----------------------------------------------------------------------------
def run_command(raw: str, cwd: str) -> tuple[str, str]:
    """Return (output_text, new_cwd). cwd is virtual /abs path."""
    line = raw.strip()
    if not line:
        return "", cwd
    # Parse: split on pipes? No — keep simple. Tokenize with quotes.
    tokens = _tokenize(line)
    if not tokens:
        return "", cwd
    cmd = tokens[0]
    args = tokens[1:]
    try:
        handler = COMMANDS.get(cmd)
        if handler is None:
            # Try external command via subprocess restricted to VFS
            if cmd in ("python3", "python"):
                return _cmd_python(args, cwd)
            return f"kemus: commande introuvable : {cmd}\nTapez 'help'.\n", cwd
        return handler(args, cwd)
    except PermissionError as e:
        return f"kemus: {e}\n", cwd
    except FileNotFoundError as e:
        return f"kemus: {cmd}: {e}\n", cwd
    except Exception as e:
        return f"kemus: {cmd}: {e}\n", cwd


def _tokenize(s: str) -> list[str]:
    """Simple shell tokenizer supporting ' and \"."""
    out = []
    cur = ""
    i = 0
    quote = None
    while i < len(s):
        c = s[i]
        if quote:
            if c == quote:
                quote = None
            else:
                cur += c
        else:
            if c in ('"', "'"):
                quote = c
            elif c.isspace():
                if cur:
                    out.append(cur)
                    cur = ""
            else:
                cur += c
        i += 1
    if cur:
        out.append(cur)
    return out


# --- Redirection helper ---
def _split_redirect(args):
    """Pull out > file or >> file from args. Returns (cmd_args, redir_target_or_None, append_bool)."""
    redir = None
    append = False
    cmd_args = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in (">", ">>") and i + 1 < len(args):
            redir = args[i + 1]
            append = (a == ">>")
            i += 2
        elif a.startswith(">"):
            redir = a.lstrip(">")
            append = a.startswith(">>")
            i += 1
        else:
            cmd_args.append(a)
            i += 1
    return cmd_args, redir, append


# ---- Commands ----
def _cmd_help(args, cwd):
    text = (
        "Commandes disponibles :\n"
        "  help                 afficher cette aide\n"
        "  pwd                  repertoire courant\n"
        "  ls [-l] [path]       lister\n"
        "  cd <path>            changer de repertoire\n"
        "  cat <file>           afficher un fichier\n"
        "  echo <text> [> f]    afficher ou ecrire\n"
        "  mkdir <dir>          creer un repertoire\n"
        "  touch <file>         creer un fichier vide\n"
        "  rm [-r] <path>       supprimer\n"
        "  mv <src> <dst>       deplacer/renommer\n"
        "  cp <src> <dst>       copier\n"
        "  find <dir>           chercher recursivement\n"
        "  tree [dir]           arborescence\n"
        "  edit <file>          ouvrir dans l'editeur (frontend)\n"
        "  date                 date courante\n"
        "  whoami               utilisateur\n"
        "  uname [-a]           noyau\n"
        "  neofetch             info systeme + logo ASCII\n"
        "  ps                   processus\n"
        "  top                  ressources\n"
        "  df                   disque\n"
        "  free                 memoire\n"
        "  history              historique\n"
        "  clear                effacer l'ecran\n"
        "  apt|dnf|pacman|nix   gestionnaires de paquets (simules)\n"
        "  python [-c CODE]     executer Python dans le sandbox\n"
        "  exit                 fermer le terminal\n"
    )
    return text, cwd


def _cmd_pwd(args, cwd):
    return cwd + "\n", cwd


def _cmd_ls(args, cwd):
    long_fmt = False
    paths = []
    for a in args:
        if a.startswith("-"):
            if "l" in a:
                long_fmt = True
        else:
            paths.append(a)
    if not paths:
        paths = ["."]
    out_lines = []
    for p in paths:
        try:
            target = safe_path(p, cwd)
        except PermissionError as e:
            out_lines.append(f"ls: {e}")
            continue
        if not target.exists():
            out_lines.append(f"ls: cannot access '{p}': No such file or directory")
            continue
        if target.is_dir():
            items = sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            if long_fmt:
                out_lines.append(f"total {len(items)}")
                for it in items:
                    st = it.stat()
                    kind = "d" if it.is_dir() else "-"
                    perms = "rwxr-xr-x"
                    size = st.st_size
                    mtime = time.strftime("%b %d %H:%M", time.localtime(st.st_mtime))
                    out_lines.append(f"{kind}{perms}  kemus  kemus  {size:>6} {mtime} {it.name}")
            else:
                names = []
                for it in items:
                    n = it.name
                    if it.is_dir():
                        names.append(n + "/")
                    else:
                        names.append(n)
                out_lines.append("  ".join(names))
        else:
            out_lines.append(p)
    return "\n".join(out_lines) + "\n", cwd


def _cmd_cd(args, cwd):
    if not args or args[0] == "~":
        return "/", "/"
    target = safe_path(args[0], cwd)
    if not target.exists():
        raise FileNotFoundError(f"{args[0]}: No such file or directory")
    if not target.is_dir():
        return f"cd: {args[0]}: Not a directory\n", cwd
    return rel_from_root(target) or "/", rel_from_root(target) or "/"


def _cmd_cat(args, cwd):
    if not args:
        return "cat: usage: cat <file>\n", cwd
    out = []
    for a in args:
        p = safe_path(a, cwd)
        if not p.exists():
            out.append(f"cat: {a}: No such file or directory")
            continue
        if p.is_dir():
            out.append(f"cat: {a}: Is a directory")
            continue
        out.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out) + ("\n" if out and not out[-1].endswith("\n") else ""), cwd


def _cmd_echo(args, cwd):
    cmd_args, redir, append = _split_redirect(args)
    text = " ".join(cmd_args) + "\n"
    if redir:
        p = safe_path(redir, cwd)
        mode = "a" if append else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(text)
        return "", cwd
    return text, cwd


def _cmd_mkdir(args, cwd):
    if not args:
        return "mkdir: usage: mkdir <dir>\n", cwd
    for a in args:
        if a.startswith("-"):
            continue
        p = safe_path(a, cwd)
        p.mkdir(parents=True, exist_ok=False)
    return "", cwd


def _cmd_touch(args, cwd):
    if not args:
        return "touch: usage: touch <file>\n", cwd
    for a in args:
        p = safe_path(a, cwd)
        if not p.exists():
            p.write_text("", encoding="utf-8")
        else:
            os.utime(p)
    return "", cwd


def _cmd_rm(args, cwd):
    recursive = False
    targets = []
    for a in args:
        if a.startswith("-"):
            if "r" in a or "R" in a:
                recursive = True
        else:
            targets.append(a)
    if not targets:
        return "rm: usage: rm [-r] <path>\n", cwd
    for a in targets:
        p = safe_path(a, cwd)
        if not p.exists():
            return f"rm: cannot remove '{a}': No such file or directory\n", cwd
        if p.is_dir():
            if not recursive:
                return f"rm: cannot remove '{a}': Is a directory (use -r)\n", cwd
            shutil.rmtree(p)
        else:
            p.unlink()
    return "", cwd


def _cmd_mv(args, cwd):
    if len(args) < 2:
        return "mv: usage: mv <src> <dst>\n", cwd
    src = safe_path(args[0], cwd)
    dst = safe_path(args[1], cwd)
    if not src.exists():
        return f"mv: cannot stat '{args[0]}': No such file or directory\n", cwd
    if dst.is_dir():
        dst = dst / src.name
    shutil.move(str(src), str(dst))
    return "", cwd


def _cmd_cp(args, cwd):
    if len(args) < 2:
        return "cp: usage: cp <src> <dst>\n", cwd
    src = safe_path(args[0], cwd)
    dst = safe_path(args[1], cwd)
    if not src.exists():
        return f"cp: cannot stat '{args[0]}': No such file or directory\n", cwd
    if dst.is_dir():
        dst = dst / src.name
    if src.is_dir():
        shutil.copytree(str(src), str(dst))
    else:
        shutil.copy2(str(src), str(dst))
    return "", cwd


def _cmd_find(args, cwd):
    base = safe_path(args[0] if args else ".", cwd)
    if not base.exists():
        return f"find: '{args[0] if args else '.'}': No such file or directory\n", cwd
    out = []
    for root, dirs, files in os.walk(base):
        for d in dirs:
            out.append(str(Path(root).relative_to(VFS_ROOT) / d))
        for f in files:
            out.append(str(Path(root).relative_to(VFS_ROOT) / f))
    return "\n".join(out) + "\n", cwd


def _cmd_tree(args, cwd):
    base = safe_path(args[0] if args else ".", cwd)
    if not base.exists() or not base.is_dir():
        return f"tree: {args[0] if args else '.'}: not a directory\n", cwd
    lines = [rel_from_root(base) or "."]
    for root, dirs, files in os.walk(base):
        rel = Path(root).relative_to(base)
        depth = 0 if str(rel) == "." else len(rel.parts)
        for d in sorted(dirs):
            lines.append("  " * depth + "├── " + d + "/")
        for f in sorted(files):
            lines.append("  " * depth + "├── " + f)
    return "\n".join(lines) + "\n", cwd


def _cmd_date(args, cwd):
    return time.strftime("%a %b %d %H:%M:%S CET %Y") + "\n", cwd


def _cmd_whoami(args, cwd):
    return "kemus\n", cwd


def _cmd_uname(args, cwd):
    if args and args[0] in ("-a", "--all"):
        return f"{DISTRO} {VERSION} {DISTRO}-Kernel-1.0.0 #1 SMP x86_64 GNU/Linux\n", cwd
    return f"{DISTRO}\n", cwd


def _cmd_neofetch(args, cwd):
    logo = (
        "        ▗▄▄▄       \n"
        "       ▗███▚       kemus@nixos\n"
        "       ▟███▙       ----------\n"
        "      ▟█████▙      OS: KemusOS 1.0 x86_64\n"
        "     ▟███████▙     Kernel: Kemus-Kernel 1.0.0\n"
        "    ▟█████████▙    Uptime: " + _uptime_str() + "\n"
        "   ▟███████████▙   Shell: kemus-sh 1.0\n"
        "  ▟█████████████▙  DE: Plasma (Kemus Edition)\n"
        " ▟███████████████▙ WM: KWin-virtual\n"
        "                    Terminal: kemus-term\n"
        "                    CPU: Virtual x86_64 (1 core)\n"
        "                    Memory: 256 MiB\n"
        "                    Disk: " + _disk_usage() + "\n"
        "                    Theme: Breeze-Dark + NixSnow\n"
        "                    Resolution: " + _resolution_str() + "\n"
    )
    return logo, cwd


def _cmd_ps(args, cwd):
    return (
        "  PID USER     COMMAND\n"
        "    1 kemus    /sbin/init\n"
        "   42 kemus    kwin-virtual\n"
        "   43 kemus    plasmashell\n"
        "   44 kemus    kemus-term\n"
        "   45 kemus    dolphin-virtual\n"
        "   46 kemus    python3 kemus.py\n"
    ), cwd


def _cmd_top(args, cwd):
    return (
        "top - " + time.strftime("%H:%M:%S") + " up " + _uptime_str() + ", 1 user\n"
        "Tasks: 6 total, 1 running, 5 sleeping\n"
        "%Cpu(s):  3.2 us,  1.1 sy,  0.0 ni, 95.7 id\n"
        "MiB Mem : 256.0 total, 180.2 free, 35.8 used, 40.0 buff/cache\n"
        "MiB Swap:   0.0 total,   0.0 free,   0.0 used\n"
        "\n"
        "  PID USER      PR  NI    VIRT    RES    CPU COMMAND\n"
        "    1 kemus     20   0    128M    12M     0.0 /sbin/init\n"
        "   46 kemus     20   0    256M    45M     1.5 python3 kemus.py\n"
        "   43 kemus     20   0    190M    38M     0.7 plasmashell\n"
    ), cwd


def _cmd_df(args, cwd):
    return (
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "kemus-vfs       512M  64M   448M  13% /home/kemus\n"
        "tmpfs           256M  0     256M   0% /tmp\n"
    ), cwd


def _cmd_free(args, cwd):
    return (
        "              total        used        free      shared  buff/cache   available\n"
        "Mem:            256          36         180           8          40         200\n"
        "Swap:             0           0           0\n"
    ), cwd


def _cmd_history(args, cwd):
    # History is client-side; return placeholder so server-side call is harmless
    return "(historique cote client)\n", cwd


def _cmd_clear(args, cwd):
    return "\x1bc", cwd  # ANSI clear — frontend detects


def _cmd_edit(args, cwd):
    if not args:
        return "edit: usage: edit <file>\n", cwd
    p = safe_path(args[0], cwd)
    rel = rel_from_root(p)
    return f"\x1bOPEN_EDITOR:{rel}\x1b", cwd


def _cmd_python(args, cwd):
    if not args:
        return "python: usage: python [-c CODE | file.py]\n", cwd
    if args[0] == "-c":
        code = " ".join(args[1:])
    else:
        p = safe_path(args[0], cwd)
        if not p.exists():
            return f"python: can't open file '{args[0]}': No such file\n", cwd
        code = p.read_text(encoding="utf-8")
    # Run in subprocess, restricted cwd = VFS_ROOT
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(VFS_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = proc.stdout + proc.stderr
        return out, cwd
    except subprocess.TimeoutExpired:
        return "python: timeout (>10s)\n", cwd
    except Exception as e:
        return f"python: {e}\n", cwd


def _make_pkg_mgr(name):
    installed = {"kde-plasma", "firefox", "kdevelop", "dolphin", "konsole", "neofetch", "git", "vim", "nano"}

    def handler(args, cwd):
        if not args:
            return f"{name}: usage: {name} <install|remove|search|list> [pkg]\n", cwd
        sub = args[0]
        if sub == "install" and len(args) > 1:
            pkg = args[1]
            if pkg in installed:
                return f"{name}: {pkg} est deja installe (plus recent).\n", cwd
            installed.add(pkg)
            return (
                f"Resolution des dependances...\n"
                f"Telechargement de {pkg}-1.0-x86_64...\n"
                f"Verification des cles...\n"
                f"Installation de {pkg}... fait.\n"
                f"\x1bTOAST:{pkg} installe avec succes\x1b\n"
            ), cwd
        if sub == "remove" and len(args) > 1:
            pkg = args[1]
            if pkg in installed:
                installed.discard(pkg)
                return f"Suppression de {pkg}... fait.\n", cwd
            return f"{name}: {pkg} n'est pas installe.\n", cwd
        if sub == "search" and len(args) > 1:
            return f"Resultats pour '{args[1]}':\n  {args[1]}-1.0 (virtual) - paquet simule\n", cwd
        if sub == "list":
            return "Paquets installes:\n  " + "\n  ".join(sorted(installed)) + "\n", cwd
        if sub in ("update", "upgrade"):
            return f"{name}: 0 paquets a mettre a jour. Tout est a jour.\n", cwd
        return f"{name}: sous-commande inconnue : {sub}\n", cwd

    return handler


def _cmd_exit(args, cwd):
    return "\x1bCLOSE_TERM\x1b", cwd


def _uptime_str():
    return "1 min"


def _disk_usage():
    try:
        total = 512 * 1024 * 1024
        used = sum(p.stat().st_size for p in VFS_ROOT.rglob("*") if p.is_file())
        return f"{used // 1024}K / 512M"
    except Exception:
        return "—"


def _resolution_str():
    return "1366x768 (virtual)"


COMMANDS = {
    "help": _cmd_help,
    "pwd": _cmd_pwd,
    "ls": _cmd_ls,
    "ll": lambda a, c: _cmd_ls(["-l"] + a, c),
    "cd": _cmd_cd,
    "cat": _cmd_cat,
    "echo": _cmd_echo,
    "mkdir": _cmd_mkdir,
    "touch": _cmd_touch,
    "rm": _cmd_rm,
    "mv": _cmd_mv,
    "cp": _cmd_cp,
    "find": _cmd_find,
    "tree": _cmd_tree,
    "date": _cmd_date,
    "whoami": _cmd_whoami,
    "uname": _cmd_uname,
    "neofetch": _cmd_neofetch,
    "ps": _cmd_ps,
    "top": _cmd_top,
    "df": _cmd_df,
    "free": _cmd_free,
    "history": _cmd_history,
    "clear": _cmd_clear,
    "edit": _cmd_edit,
    "python": _cmd_python,
    "python3": _cmd_python,
    "apt": _make_pkg_mgr("apt"),
    "dnf": _make_pkg_mgr("dnf"),
    "pacman": lambda a, c: _make_pkg_mgr("pacman")(["-S" if a and a[0] in ("-S", "-Syu") else (a[0][1:] if a and a[0].startswith("-") else "list")] + a[1:], c),
    "nix": _make_pkg_mgr("nix"),
    "exit": _cmd_exit,
    "logout": _cmd_exit,
}


# ----------------------------------------------------------------------------
# Session management
# ----------------------------------------------------------------------------
def get_or_create_session(token: str | None) -> str:
    if token and token in SESSIONS:
        return token
    new = secrets.token_hex(16)
    SESSIONS[new] = {"cwd": "/", "history": [], "created": time.time()}
    return new


# ----------------------------------------------------------------------------
# HTTP Handler
# ----------------------------------------------------------------------------
class KemusHandler(BaseHTTPRequestHandler):
    server_version = f"Kemus/{VERSION}"
    sys_version = ""

    def log_message(self, fmt, *args):
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.address_string()} {fmt % args}\n")
        except Exception:
            pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200, headers=None):
        self._send(code, json.dumps(obj), headers=headers)

    def _cookie_token(self):
        c = self.headers.get("Cookie", "")
        for part in c.split(";"):
            part = part.strip()
            if part.startswith("kemus_session="):
                return part.split("=", 1)[1]
        return None

    def do_GET(self):
        try:
            u = urlparse(self.path)
            path = u.path
            if path == "/" or path == "/index.html":
                self._send(200, HTML_PAGE, ctype="text/html; charset=utf-8")
                return
            if path == "/api/health":
                self._json({"ok": True, "version": VERSION, "uptime": time.time() - START_TIME})
                return
            if path == "/api/desktop":
                self._json({
                    "distro": DISTRO,
                    "version": VERSION,
                    "user": "kemus",
                    "host": socket.gethostname(),
                    "port": PORT,
                })
                return
            if path == "/api/fs":
                token = get_or_create_session(self._cookie_token())
                cwd = SESSIONS[token]["cwd"]
                q = parse_qs(u.query)
                rel = q.get("path", [cwd])[0]
                try:
                    p = safe_path(rel, cwd)
                except PermissionError as e:
                    self._json({"error": str(e)}, 403)
                    return
                if not p.exists():
                    self._json({"error": "not found"}, 404)
                    return
                if not p.is_dir():
                    self._json({"error": "not a directory"}, 400)
                    return
                items = []
                for it in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    items.append({
                        "name": it.name,
                        "type": "dir" if it.is_dir() else "file",
                        "size": it.stat().st_size if it.is_file() else 0,
                    })
                self._json({"path": rel_from_root(p), "items": items}, headers={"Set-Cookie": f"kemus_session={token}; Path=/; HttpOnly"})
                return
            if path == "/api/file":
                q = parse_qs(u.query)
                rel = q.get("path", [None])[0]
                if not rel:
                    self._json({"error": "path required"}, 400)
                    return
                token = get_or_create_session(self._cookie_token())
                cwd = SESSIONS[token]["cwd"]
                try:
                    p = safe_path(rel, cwd)
                except PermissionError as e:
                    self._json({"error": str(e)}, 403)
                    return
                if not p.exists() or not p.is_file():
                    self._json({"error": "not found"}, 404)
                    return
                content = p.read_text(encoding="utf-8", errors="replace")
                self._json({"path": rel, "content": content})
                return
            self._send(404, "Not Found", ctype="text/plain")
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            data = json.loads(body.decode("utf-8") or "{}")
            token = get_or_create_session(self._cookie_token() or data.get("session"))
            if u.path == "/api/terminal":
                cmd = data.get("cmd", "")
                cwd = data.get("cwd", SESSIONS[token]["cwd"])
                if cwd == "~" or not cwd:
                    cwd = "/"
                SESSIONS[token]["cwd"] = cwd
                SESSIONS[token]["history"].append(cmd)
                if len(SESSIONS[token]["history"]) > 500:
                    SESSIONS[token]["history"] = SESSIONS[token]["history"][-500:]
                output, new_cwd = run_command(cmd, cwd)
                SESSIONS[token]["cwd"] = new_cwd
                self._json({
                    "output": output,
                    "cwd": new_cwd,
                    "session": token,
                    "prompt": _prompt(new_cwd),
                }, headers={"Set-Cookie": f"kemus_session={token}; Path=/; HttpOnly"})
                return
            if u.path == "/api/file/save":
                rel = data.get("path")
                content = data.get("content", "")
                if not rel:
                    self._json({"error": "path required"}, 400)
                    return
                cwd = SESSIONS[token]["cwd"]
                p = safe_path(rel, cwd)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                self._json({"ok": True, "path": rel, "size": len(content)})
                return
            self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, 500)


def _prompt(cwd: str) -> str:
    short = cwd if cwd == "/" else cwd
    if short == "/" or short == "":
        short = "~"
    elif not short.startswith("~"):
        short = "~" + ("" if short == "/" else short)
    return f"\x1b[32mkemus@nixos\x1b[0m:\x1b[34m{short}\x1b[0m$ "


START_TIME = time.time()


# ----------------------------------------------------------------------------
# Frontend (single embedded HTML page)
# ----------------------------------------------------------------------------
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kemus - PC virtuel</title>
<style>
:root {
  --bg: #0b0f19;
  --panel: rgba(20, 26, 40, 0.92);
  --panel-solid: #141a28;
  --accent: #3daee9;
  --accent-2: #7eb4e6;
  --nix: #5277c3;
  --fedora: #294172;
  --text: #e6e9ef;
  --text-dim: #9aa4b8;
  --win-bg: #1c2230;
  --win-border: #2a3142;
  --term-bg: #0a0d14;
  --term-fg: #c5d0e6;
  --green: #27ae60;
  --red: #e74c3c;
  --yellow: #f1c40f;
  --shadow: 0 8px 28px rgba(0,0,0,0.55);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  height: 100%; width: 100%; overflow: hidden;
  font-family: "Inter", "Segoe UI", "Noto Sans", system-ui, sans-serif;
  color: var(--text); background: var(--bg); user-select: none;
}
#desktop {
  position: fixed; inset: 0;
  background:
    radial-gradient(ellipse at 30% 20%, rgba(82,119,195,0.35), transparent 60%),
    radial-gradient(ellipse at 75% 80%, rgba(41,65,114,0.45), transparent 55%),
    linear-gradient(135deg, #0b0f19 0%, #11182b 50%, #1a2138 100%);
  overflow: hidden;
}
#desktop::before {
  content: ""; position: absolute; inset: 0;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'><g fill='none' stroke='%235277c3' stroke-width='1.2' opacity='0.06'><path d='M100 20 L130 70 L100 100 L70 70 Z'/><path d='M100 100 L130 130 L100 180 L70 130 Z'/><path d='M100 100 L160 100 L100 130 L40 100 Z'/><path d='M100 100 L160 100 L100 70 L40 100 Z'/></g></svg>");
  background-size: 360px 360px;
  pointer-events: none; opacity: 0.5;
}
/* ---- Top panel (KDE Plasma style) ---- */
#top-panel {
  position: fixed; top: 0; left: 0; right: 0; height: 28px;
  background: var(--panel); backdrop-filter: blur(14px);
  border-bottom: 1px solid rgba(255,255,255,0.06);
  display: flex; align-items: center; padding: 0 8px; z-index: 10000;
  font-size: 13px;
}
#kickoff {
  width: 28px; height: 22px; display: flex; align-items: center; justify-content: center;
  cursor: pointer; border-radius: 4px;
}
#kickoff:hover { background: rgba(255,255,255,0.08); }
#kickoff svg { width: 18px; height: 18px; }
#window-list { flex: 1; display: flex; align-items: center; gap: 2px; padding: 0 6px; overflow: hidden; }
.win-tab {
  padding: 3px 10px; background: rgba(255,255,255,0.05); border-radius: 4px;
  cursor: pointer; font-size: 12px; max-width: 160px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.win-tab.active { background: var(--accent); color: #fff; }
.win-tab:hover { background: rgba(255,255,255,0.12); }
#sys-tray { display: flex; align-items: center; gap: 10px; padding: 0 6px; color: var(--text-dim); }
#sys-tray .tray-item { cursor: pointer; padding: 2px 4px; border-radius: 3px; }
#sys-tray .tray-item:hover { background: rgba(255,255,255,0.08); color: var(--text); }
#clock { color: var(--text); font-variant-numeric: tabular-nums; font-size: 12px; min-width: 120px; text-align: right; }
/* ---- Bottom panel (icons) ---- */
#dock {
  position: fixed; bottom: 0; left: 50%; transform: translateX(-50%);
  height: 52px; padding: 6px 10px; background: var(--panel);
  backdrop-filter: blur(14px); border-radius: 12px 12px 0 0;
  border: 1px solid rgba(255,255,255,0.06); border-bottom: none;
  display: flex; align-items: center; gap: 6px; z-index: 10000;
}
.dock-icon {
  width: 40px; height: 40px; border-radius: 8px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,0.04); transition: transform 0.15s, background 0.15s;
}
.dock-icon:hover { background: rgba(61,174,233,0.25); transform: translateY(-4px); }
.dock-icon svg { width: 24px; height: 24px; }
/* ---- Kickoff menu ---- */
#kickoff-menu {
  position: fixed; top: 30px; left: 6px; width: 380px; max-height: 70vh;
  background: var(--panel-solid); border: 1px solid var(--win-border);
  border-radius: 8px; box-shadow: var(--shadow); padding: 12px;
  display: none; flex-direction: column; z-index: 20000; overflow: hidden;
}
#kickoff-menu.open { display: flex; }
.km-search {
  width: 100%; padding: 8px 12px; background: #0d121d;
  border: 1px solid var(--win-border); border-radius: 4px;
  color: var(--text); font-size: 13px; margin-bottom: 10px;
}
.km-search:focus { outline: none; border-color: var(--accent); }
.km-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; overflow-y: auto; }
.km-app {
  padding: 10px 6px; border-radius: 6px; cursor: pointer; text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  font-size: 11px; color: var(--text-dim);
}
.km-app:hover { background: rgba(61,174,233,0.18); color: var(--text); }
.km-app svg { width: 28px; height: 28px; }
.km-footer {
  margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--win-border);
  display: flex; justify-content: space-between; align-items: center;
  font-size: 12px; color: var(--text-dim);
}
.km-power { display: flex; gap: 4px; }
.km-power button {
  background: rgba(255,255,255,0.06); border: none; color: var(--text);
  width: 28px; height: 28px; border-radius: 4px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.km-power button:hover { background: var(--accent); }
.km-power button svg { width: 14px; height: 14px; }
/* ---- Desktop icons ---- */
#desktop-icons { position: absolute; top: 40px; left: 12px; display: flex; flex-direction: column; gap: 10px; }
.desk-icon {
  width: 80px; padding: 8px 4px; border-radius: 6px; cursor: pointer;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  font-size: 11px; color: var(--text); text-align: center;
}
.desk-icon:hover { background: rgba(255,255,255,0.08); }
.desk-icon svg { width: 36px; height: 36px; }
/* ---- Windows ---- */
.window {
  position: absolute; min-width: 240px; min-height: 120px;
  background: var(--win-bg); border: 1px solid var(--win-border);
  border-radius: 6px; box-shadow: var(--shadow);
  display: flex; flex-direction: column; overflow: hidden;
}
.window.maximized { border-radius: 0; }
.win-titlebar {
  height: 30px; background: #232a3a; display: flex; align-items: center;
  padding: 0 6px 0 10px; cursor: grab; user-select: none;
  border-bottom: 1px solid var(--win-border);
}
.win-titlebar.dragging { cursor: grabbing; }
.win-title { flex: 1; font-size: 12px; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.win-controls { display: flex; gap: 4px; }
.win-btn {
  width: 18px; height: 18px; border-radius: 3px; border: none;
  background: rgba(255,255,255,0.06); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: var(--text-dim); font-size: 11px;
}
.win-btn:hover { background: rgba(255,255,255,0.18); color: var(--text); }
.win-btn.close:hover { background: var(--red); color: #fff; }
.win-body { flex: 1; overflow: auto; position: relative; }
.win-resize {
  position: absolute; right: 0; bottom: 0; width: 14px; height: 14px;
  cursor: nwse-resize; z-index: 5;
}
/* ---- Terminal ---- */
.term {
  height: 100%; background: var(--term-bg); color: var(--term-fg);
  font-family: "JetBrains Mono", "Fira Code", "Consolas", "Noto Sans Mono", monospace;
  font-size: 13px; padding: 8px; overflow-y: auto; cursor: text;
}
.term-line { white-space: pre-wrap; word-break: break-word; }
.term-input-line { display: flex; align-items: baseline; }
.term-prompt { white-space: pre; color: var(--accent-2); }
.term-prompt .user { color: var(--green); }
.term-prompt .path { color: var(--accent); }
.term-input {
  flex: 1; background: transparent; border: none; outline: none;
  color: var(--term-fg); font: inherit; padding: 0; margin-left: 4px;
}
/* ---- File manager ---- */
.files { display: flex; height: 100%; }
.files-sidebar { width: 160px; background: #161b29; padding: 8px; border-right: 1px solid var(--win-border); overflow-y: auto; }
.files-sidebar h4 { font-size: 11px; color: var(--text-dim); margin: 8px 0 4px; text-transform: uppercase; }
.files-side-item {
  padding: 5px 8px; border-radius: 4px; cursor: pointer; font-size: 12px;
  display: flex; align-items: center; gap: 6px; color: var(--text);
}
.files-side-item:hover { background: rgba(255,255,255,0.06); }
.files-side-item.active { background: var(--accent); color: #fff; }
.files-main { flex: 1; padding: 12px; overflow-y: auto; }
.files-toolbar { display: flex; gap: 6px; margin-bottom: 8px; align-items: center; font-size: 12px; }
.files-toolbar button {
  background: rgba(255,255,255,0.05); border: 1px solid var(--win-border);
  color: var(--text); padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px;
}
.files-toolbar button:hover { background: rgba(61,174,233,0.2); }
.files-path { flex: 1; color: var(--text-dim); padding: 4px 8px; }
.files-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
.file-item {
  padding: 10px 6px; border-radius: 4px; cursor: pointer; text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
}
.file-item:hover { background: rgba(61,174,233,0.15); }
.file-item svg { width: 36px; height: 36px; }
.file-item .name { font-size: 11px; color: var(--text); word-break: break-word; }
/* ---- Editor ---- */
.editor { display: flex; flex-direction: column; height: 100%; }
.editor-toolbar { display: flex; gap: 6px; padding: 6px; background: #161b29; border-bottom: 1px solid var(--win-border); align-items: center; font-size: 12px; }
.editor-toolbar input { flex: 1; background: #0d121d; border: 1px solid var(--win-border); border-radius: 4px; color: var(--text); padding: 4px 8px; font-size: 12px; }
.editor-toolbar button { background: var(--accent); border: none; color: #fff; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
.editor-toolbar button:hover { background: var(--accent-2); }
.editor textarea {
  flex: 1; background: #0a0d14; color: var(--term-fg); border: none; outline: none;
  font-family: "JetBrains Mono", "Consolas", monospace; font-size: 13px;
  padding: 10px; resize: none; line-height: 1.5;
}
.editor-status { padding: 3px 8px; background: #161b29; border-top: 1px solid var(--win-border); font-size: 11px; color: var(--text-dim); display: flex; justify-content: space-between; }
/* ---- Settings ---- */
.settings { padding: 16px; overflow-y: auto; }
.settings h2 { font-size: 16px; margin-bottom: 10px; }
.settings .row { padding: 8px 0; border-bottom: 1px solid var(--win-border); display: flex; justify-content: space-between; font-size: 13px; }
.settings .row span:last-child { color: var(--accent); }
/* ---- Help / About ---- */
.help { padding: 16px; font-size: 13px; line-height: 1.6; }
.help h2 { color: var(--accent); margin-bottom: 8px; }
.help code { background: #0d121d; padding: 1px 5px; border-radius: 3px; color: var(--accent-2); }
.help ul { padding-left: 20px; }
/* ---- Browser (mock) ---- */
.browser { display: flex; flex-direction: column; height: 100%; }
.browser-toolbar { display: flex; gap: 4px; padding: 6px; background: #161b29; border-bottom: 1px solid var(--win-border); }
.browser-toolbar input { flex: 1; background: #0d121d; border: 1px solid var(--win-border); border-radius: 4px; color: var(--text); padding: 4px 8px; font-size: 12px; }
.browser-toolbar button { background: rgba(255,255,255,0.05); border: 1px solid var(--win-border); color: var(--text); padding: 4px 8px; border-radius: 4px; cursor: pointer; }
.browser-frame { flex: 1; background: #fff; }
/* ---- Calculator ---- */
.calc { padding: 8px; background: #161b29; height: 100%; display: flex; flex-direction: column; gap: 4px; }
.calc-display { background: #0a0d14; color: var(--term-fg); padding: 10px; border-radius: 4px; text-align: right; font-family: monospace; font-size: 18px; min-height: 40px; }
.calc-grid { flex: 1; display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
.calc-grid button { background: #232a3a; border: none; color: var(--text); font-size: 16px; border-radius: 4px; cursor: pointer; }
.calc-grid button:hover { background: #2d3650; }
.calc-grid button.op { background: var(--accent); }
.calc-grid button.eq { background: var(--green); color: #fff; }
/* ---- Toast ---- */
#toast-wrap { position: fixed; bottom: 70px; right: 12px; display: flex; flex-direction: column; gap: 6px; z-index: 30000; }
.toast {
  background: var(--panel-solid); border-left: 3px solid var(--green);
  color: var(--text); padding: 8px 14px; border-radius: 4px; font-size: 12px;
  box-shadow: var(--shadow); animation: slideIn 0.2s ease;
}
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
/* ---- Boot screen ---- */
#boot {
  position: fixed; inset: 0; background: #0b0f19; z-index: 99999;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 20px;
}
#boot .logo svg { width: 100px; height: 100px; }
#boot .progress { width: 200px; height: 4px; background: #1a2138; border-radius: 2px; overflow: hidden; }
#boot .progress-bar { height: 100%; background: var(--accent); width: 0%; transition: width 0.3s; }
#boot .text { color: var(--text-dim); font-size: 12px; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.3); }
</style>
</head>
<body>
<div id="boot">
  <div class="logo">
    <svg viewBox="0 0 200 200"><g fill="none" stroke="#5277c3" stroke-width="2"><path d="M100 20 L130 70 L100 100 L70 70 Z"/><path d="M100 100 L130 130 L100 180 L70 130 Z"/><path d="M100 100 L160 100 L100 130 L40 100 Z"/><path d="M100 100 L160 100 L100 70 L40 100 Z"/></g><circle cx="100" cy="100" r="4" fill="#3daee9"/></svg>
  </div>
  <div class="progress"><div class="progress-bar" id="boot-bar"></div></div>
  <div class="text" id="boot-text">Demarrage de KemusOS 1.0...</div>
</div>

<div id="desktop">
  <div id="top-panel">
    <div id="kickoff" title="Menu">
      <svg viewBox="0 0 200 200"><g fill="none" stroke="#7eb4e6" stroke-width="6"><path d="M100 20 L130 70 L100 100 L70 70 Z"/><path d="M100 100 L130 130 L100 180 L70 130 Z"/><path d="M100 100 L160 100 L100 130 L40 100 Z"/><path d="M100 100 L160 100 L100 70 L40 100 Z"/></g></svg>
    </div>
    <div id="window-list"></div>
    <div id="sys-tray">
      <span class="tray-item" title="Reseau">📶</span>
      <span class="tray-item" title="Volume">🔊</span>
      <span class="tray-item" title="Batterie">🔋</span>
      <span id="clock">--:--:--</span>
    </div>
  </div>

  <div id="desktop-icons"></div>

  <div id="kickoff-menu">
    <input class="km-search" placeholder="Rechercher une application..." id="km-search-input">
    <div class="km-grid" id="km-grid"></div>
    <div class="km-footer">
      <span>kemus @ nixos</span>
      <div class="km-power">
        <button title="Verrouiller" onclick="toast('Verrouillage simule')">🔒</button>
        <button title="Redemarrer" onclick="if(confirm('Redemarrer KemusOS ?')) location.reload()">🔄</button>
        <button title="Eteindre" onclick="if(confirm('Eteindre KemusOS ?')) document.getElementById('desktop').style.display='none'">⏻</button>
      </div>
    </div>
  </div>

  <div id="dock"></div>
  <div id="toast-wrap"></div>
</div>

<script>
// ====== State ======
let zTop = 100;
let windows = {};
let winSeq = 0;
let activeWin = null;
let session = null;

// ====== API helpers ======
async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" }, credentials: "same-origin" };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  const text = await r.text();
  let json = null; try { json = JSON.parse(text); } catch (e) {}
  if (!r.ok && json) throw new Error(json.error || r.status);
  return json || {};
}

// ====== Toast ======
function toast(msg) {
  const w = document.getElementById("toast-wrap");
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  w.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity 0.4s"; setTimeout(() => t.remove(), 400); }, 3000);
}

// ====== Apps registry ======
const APPS = {
  terminal: { name: "Terminal", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#27ae60" stroke-width="2"><rect x="2" y="3" width="20" height="18" rx="2"/><path d="M6 8 L10 12 L6 16"/><line x1="12" y1="16" x2="18" y2="16"/></svg>', open: openTerminal },
  files: { name: "Fichiers", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#f1c40f" stroke-width="2"><path d="M3 7 L3 19 L21 19 L21 9 L12 9 L9 7 Z"/></svg>', open: openFiles },
  editor: { name: "Editeur", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#3daee9" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5 a2.121 2.121 0 0 1 3 3 L7 19 l-4 1 1 -4 Z"/></svg>', open: () => openEditor(null) },
  settings: { name: "Parametres", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#9aa4b8" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>', open: openSettings },
  help: { name: "Aide", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#e74c3c" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12" y2="17"/></svg>', open: openHelp },
  browser: { name: "Navigateur", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#7eb4e6" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2 a15 15 0 0 1 4 10 a15 15 0 0 1 -4 10 a15 15 0 0 1 -4 -10 a15 15 0 0 1 4 -10 z"/></svg>', open: openBrowser },
  calc: { name: "Calculatrice", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#f1c40f" stroke-width="2"><rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><line x1="8" y1="10" x2="8" y2="10"/><line x1="12" y1="10" x2="12" y2="10"/><line x1="16" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="8" y2="14"/><line x1="12" y1="14" x2="12" y2="14"/><line x1="16" y1="14" x2="16" y2="14"/><line x1="8" y1="18" x2="16" y2="18"/></svg>', open: openCalc },
  neofetch: { name: "Systeme", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#5277c3" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>', open: () => openTerminalWith("neofetch") },
};

// ====== Window manager ======
function createWindow(opts) {
  const id = "win" + (++winSeq);
  const w = document.createElement("div");
  w.className = "window";
  w.id = id;
  w.style.left = (60 + (winSeq * 28) % 200) + "px";
  w.style.top = (50 + (winSeq * 28) % 120) + "px";
  w.style.width = (opts.width || 560) + "px";
  w.style.height = (opts.height || 380) + "px";
  w.style.zIndex = ++zTop;
  w.innerHTML = `
    <div class="win-titlebar">
      <span class="win-title">${opts.title}</span>
      <div class="win-controls">
        <button class="win-btn min" title="Reduire">—</button>
        <button class="win-btn max" title="Agrandir">▢</button>
        <button class="win-btn close" title="Fermer">✕</button>
      </div>
    </div>
    <div class="win-body"></div>
    <div class="win-resize"></div>
  `;
  document.getElementById("desktop").appendChild(w);
  const body = w.querySelector(".win-body");
  if (typeof opts.content === "string") body.innerHTML = opts.content;
  else if (opts.content) body.appendChild(opts.content);

  // Controls
  w.querySelector(".close").onclick = (e) => { e.stopPropagation(); closeWindow(id); };
  w.querySelector(".min").onclick = (e) => { e.stopPropagation(); w.style.display = "none"; updateTaskbar(); };
  w.querySelector(".max").onclick = (e) => { e.stopPropagation(); toggleMax(id); };
  w.querySelector(".win-titlebar").addEventListener("mousedown", (e) => startDrag(id, e));
  w.querySelector(".win-resize").addEventListener("mousedown", (e) => startResize(id, e));
  w.addEventListener("mousedown", () => focusWindow(id));

  windows[id] = { el: w, title: opts.title, minimized: false, maximized: false, prev: null };
  focusWindow(id);
  updateTaskbar();
  return { id, body };
}

function focusWindow(id) {
  if (!windows[id]) return;
  windows[id].el.style.zIndex = ++zTop;
  activeWin = id;
  updateTaskbar();
}

function closeWindow(id) {
  if (!windows[id]) return;
  windows[id].el.remove();
  delete windows[id];
  updateTaskbar();
}

function toggleMax(id) {
  const w = windows[id]; if (!w) return;
  if (w.maximized) {
    const p = w.prev;
    w.el.style.left = p.left; w.el.style.top = p.top;
    w.el.style.width = p.width; w.el.style.height = p.height;
    w.el.classList.remove("maximized");
    w.maximized = false;
  } else {
    w.prev = { left: w.el.style.left, top: w.el.style.top, width: w.el.style.width, height: w.el.style.height };
    w.el.style.left = "0"; w.el.style.top = "28px";
    w.el.style.width = "100%"; w.el.style.height = "calc(100% - 80px)";
    w.el.classList.add("maximized");
    w.maximized = true;
  }
}

function startDrag(id, e) {
  if (e.target.classList.contains("win-btn")) return;
  const w = windows[id]; if (!w || w.maximized) return;
  focusWindow(id);
  const tb = windows[id].el.querySelector(".win-titlebar");
  tb.classList.add("dragging");
  const startX = e.clientX, startY = e.clientY;
  const left = parseInt(w.el.style.left), top = parseInt(w.el.style.top);
  const onMove = (ev) => {
    w.el.style.left = (left + ev.clientX - startX) + "px";
    w.el.style.top = Math.max(28, top + ev.clientY - startY) + "px";
  };
  const onUp = () => {
    tb.classList.remove("dragging");
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
  e.preventDefault();
}

function startResize(id, e) {
  e.stopPropagation();
  const w = windows[id]; if (!w) return;
  const startX = e.clientX, startY = e.clientY;
  const width = parseInt(w.el.style.width), height = parseInt(w.el.style.height);
  const onMove = (ev) => {
    w.el.style.width = Math.max(240, width + ev.clientX - startX) + "px";
    w.el.style.height = Math.max(120, height + ev.clientY - startY) + "px";
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function updateTaskbar() {
  const list = document.getElementById("window-list");
  list.innerHTML = "";
  Object.entries(windows).forEach(([id, w]) => {
    const tab = document.createElement("div");
    tab.className = "win-tab" + (id === activeWin && !w.minimized ? " active" : "");
    tab.textContent = w.title;
    tab.onclick = () => {
      if (w.minimized || w.el.style.display === "none") {
        w.el.style.display = "flex"; w.minimized = false; focusWindow(id);
      } else if (id === activeWin) {
        w.el.style.display = "none"; w.minimized = true; updateTaskbar();
      } else { focusWindow(id); }
    };
    list.appendChild(tab);
  });
}

// ====== Terminal ======
function openTerminal() {
  const { id, body } = createWindow({ title: "Terminal — kemus@nixos", width: 640, height: 400 });
  body.innerHTML = `<div class="term" id="term-${id}"></div>`;
  initTerminal(id);
}

function openTerminalWith(cmd) {
  const { id, body } = createWindow({ title: "Terminal — kemus@nixos", width: 640, height: 400 });
  body.innerHTML = `<div class="term" id="term-${id}"></div>`;
  initTerminal(id, cmd);
}

function initTerminal(id, initialCmd) {
  const termEl = document.getElementById("term-" + id);
  let cwd = "/";
  let history = [];
  let histIdx = -1;
  const writeLine = (html, plain) => {
    const div = document.createElement("div");
    div.className = "term-line";
    div.innerHTML = html;
    termEl.appendChild(div);
    termEl.scrollTop = termEl.scrollHeight;
  };
  const writeRaw = (text) => {
    // Convert ANSI basic + escape sequences
    let html = text
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/\x1b\[32m/g, '<span style="color:#27ae60">')
      .replace(/\x1b\[34m/g, '<span style="color:#3daee9">')
      .replace(/\x1b\[31m/g, '<span style="color:#e74c3c">')
      .replace(/\x1b\[33m/g, '<span style="color:#f1c40f">')
      .replace(/\x1b\[36m/g, '<span style="color:#7eb4e6">')
      .replace(/\x1b\[0m/g, "</span>")
      .replace(/\x1bc/g, ""); // clear
    // Custom escapes:
    if (text.includes("\x1bCLOSE_TERM\x1b")) { closeWindow(id); return; }
    if (text.includes("\x1bOPEN_EDITOR:")) {
      const m = text.match(/\x1bOPEN_EDITOR:([^\x1b]+)\x1b/);
      if (m) openEditor(m[1]);
      html = html.replace(/\x1bOPEN_EDITOR:[^\x1b]+\x1b/g, "");
    }
    if (text.includes("\x1bTOAST:")) {
      const m = text.match(/\x1bTOAST:([^\x1b]+)\x1b/);
      if (m) toast(m[1]);
      html = html.replace(/\x1bTOAST:[^\x1b]+\x1b/g, "");
    }
    const div = document.createElement("div");
    div.className = "term-line";
    div.innerHTML = html;
    termEl.appendChild(div);
    termEl.scrollTop = termEl.scrollHeight;
  };

  const promptHTML = (cwdDisp) => {
    let d = cwdDisp === "/" ? "~" : cwdDisp;
    return `<span class="user">kemus@nixos</span>:<span class="path">${d}</span>$ `;
  };

  const newInputLine = () => {
    const line = document.createElement("div");
    line.className = "term-input-line";
    line.innerHTML = `<span class="term-prompt">${promptHTML(cwd)}</span><input class="term-input" autocomplete="off" spellcheck="false">`;
    termEl.appendChild(line);
    const input = line.querySelector("input");
    input.focus();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const cmd = input.value;
        input.disabled = true;
        if (cmd.trim()) { history.push(cmd); histIdx = history.length; }
        runCmd(cmd);
      } else if (e.key === "ArrowUp") {
        if (histIdx > 0) { histIdx--; input.value = history[histIdx] || ""; }
        e.preventDefault();
      } else if (e.key === "ArrowDown") {
        if (histIdx < history.length - 1) { histIdx++; input.value = history[histIdx] || ""; }
        else { histIdx = history.length; input.value = ""; }
        e.preventDefault();
      } else if (e.key === "Tab") {
        e.preventDefault();
      } else if (e.ctrlKey && e.key === "l") {
        e.preventDefault();
        termEl.innerHTML = "";
        newInputLine();
      } else if (e.ctrlKey && e.key === "c") {
        input.disabled = true;
        writeLine('<span style="color:#e74c3c">^C</span>');
        newInputLine();
      }
    });
    termEl.scrollTop = termEl.scrollHeight;
  };

  const runCmd = async (cmd) => {
    if (cmd.trim() === "clear") { termEl.innerHTML = ""; newInputLine(); return; }
    try {
      const r = await api("POST", "/api/terminal", { cmd, cwd, session });
      session = r.session;
      if (r.output && r.output.includes("\x1bc")) {
        termEl.innerHTML = "";
      } else if (r.output) {
        writeRaw(r.output);
      }
      cwd = r.cwd;
    } catch (e) {
      writeRaw("Erreur: " + e.message + "\n");
    }
    newInputLine();
  };

  termEl.addEventListener("click", () => {
    const inputs = termEl.querySelectorAll("input.term-input");
    inputs[inputs.length - 1]?.focus();
  });

  // Welcome
  writeRaw("KemusOS 1.0 - Terminal virtuel\n");
  writeRaw("Tapez 'help' pour la liste des commandes.\n\n");
  newInputLine();

  if (initialCmd) {
    setTimeout(() => {
      const lastInput = termEl.querySelector("input.term-input");
      if (lastInput) { lastInput.value = initialCmd; lastInput.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" })); }
    }, 50);
  }
}

// ====== File manager ======
async function openFiles() {
  const { id, body } = createWindow({ title: "Fichiers —kemus", width: 700, height: 460 });
  body.innerHTML = `
    <div class="files">
      <div class="files-sidebar">
        <h4>Emplacements</h4>
        <div class="files-side-item" data-path="/">🏠 Accueil</div>
        <div class="files-side-item" data-path="/Documents">📄 Documents</div>
        <div class="files-side-item" data-path="/Downloads">⬇️ Telechargements</div>
        <div class="files-side-item" data-path="/Pictures">🖼️ Images</div>
        <div class="files-side-item" data-path="/Music">🎵 Musique</div>
        <div class="files-side-item" data-path="/Projects">💻 Projets</div>
      </div>
      <div class="files-main">
        <div class="files-toolbar">
          <button id="files-up">↑</button>
          <button id="files-newdir">+ Dossier</button>
          <button id="files-newfile">+ Fichier</button>
          <span class="files-path" id="files-path">/</span>
        </div>
        <div class="files-grid" id="files-grid"></div>
      </div>
    </div>`;
  let cwd = "/";
  const refresh = async () => {
    body.querySelector("#files-path").textContent = cwd === "/" ? "/ (Accueil)" : cwd;
    try {
      const r = await api("GET", "/api/fs?path=" + encodeURIComponent(cwd));
      const grid = body.querySelector("#files-grid");
      grid.innerHTML = "";
      if (r.items.length === 0) {
        grid.innerHTML = '<div style="color:#9aa4b8;grid-column:1/-1;text-align:center;padding:30px">Dossier vide</div>';
      }
      r.items.forEach(it => {
        const div = document.createElement("div");
        div.className = "file-item";
        const icon = it.type === "dir"
          ? '<svg viewBox="0 0 24 24" fill="#f1c40f"><path d="M3 7 L3 19 L21 19 L21 9 L12 9 L9 7 Z"/></svg>'
          : '<svg viewBox="0 0 24 24" fill="#3daee9"><path d="M6 2 H14 L18 6 V22 H6 Z"/></svg>';
        div.innerHTML = icon + '<div class="name">' + it.name + '</div>';
        div.ondblclick = () => {
          if (it.type === "dir") {
            cwd = (cwd === "/" ? "" : cwd) + "/" + it.name;
            refresh();
          } else {
            openEditor((cwd === "/" ? "" : cwd) + "/" + it.name);
          }
        };
        grid.appendChild(div);
      });
    } catch (e) { toast("Erreur: " + e.message); }
  };
  body.querySelectorAll(".files-side-item").forEach(el => {
    el.onclick = () => { cwd = el.dataset.path; refresh(); };
  });
  body.querySelector("#files-up").onclick = () => {
    if (cwd === "/") return;
    const parts = cwd.split("/").filter(Boolean);
    parts.pop();
    cwd = "/" + parts.join("/");
    if (cwd === "/") cwd = "/";
    refresh();
  };
  body.querySelector("#files-newdir").onclick = async () => {
    const name = prompt("Nom du dossier :");
    if (!name) return;
    try {
      await api("POST", "/api/terminal", { cmd: `mkdir ${name}`, cwd });
      refresh();
    } catch (e) { toast(e.message); }
  };
  body.querySelector("#files-newfile").onclick = async () => {
    const name = prompt("Nom du fichier :");
    if (!name) return;
    try {
      await api("POST", "/api/terminal", { cmd: `touch ${name}`, cwd });
      refresh();
    } catch (e) { toast(e.message); }
  };
  refresh();
}

// ====== Editor ======
async function openEditor(path) {
  const { id, body } = createWindow({ title: path ? ("Editeur — " + path) : "Editeur — sans titre", width: 600, height: 420 });
  body.innerHTML = `
    <div class="editor">
      <div class="editor-toolbar">
        <input id="ed-path" value="${path || ""}" placeholder="/chemin/du/fichier">
        <button id="ed-save">Enregistrer</button>
      </div>
      <textarea id="ed-text" spellcheck="false" placeholder="Saisissez votre texte..."></textarea>
      <div class="editor-status"><span id="ed-info">Pret</span><span id="ed-count">0 caract.</span></div>
    </div>`;
  const ta = body.querySelector("#ed-text");
  const info = body.querySelector("#ed-info");
  const count = body.querySelector("#ed-count");
  if (path) {
    try {
      const r = await api("GET", "/api/file?path=" + encodeURIComponent(path));
      ta.value = r.content; info.textContent = "Charge : " + path;
    } catch (e) { info.textContent = "Nouveau fichier"; }
  }
  const updateCount = () => count.textContent = ta.value.length + " caract.";
  ta.addEventListener("input", updateCount); updateCount();
  body.querySelector("#ed-save").onclick = async () => {
    const p = body.querySelector("#ed-path").value.trim();
    if (!p) { toast("Indiquez un chemin"); return; }
    try {
      await api("POST", "/api/file/save", { path: p, content: ta.value });
      info.textContent = "Enregistre : " + p + " (" + ta.value.length + " octets)";
      toast("Fichier enregistre");
    } catch (e) { toast(e.message); }
  };
}

// ====== Settings ======
async function openSettings() {
  const { id, body } = createWindow({ title: "Parametres", width: 480, height: 380 });
  let info = {};
  try { info = await api("GET", "/api/desktop"); } catch (e) {}
  body.innerHTML = `
    <div class="settings">
      <h2>Systeme</h2>
      <div class="row"><span>Distribution</span><span>${info.distro || "KemusOS"}</span></div>
      <div class="row"><span>Version</span><span>${info.version || "1.0"}</span></div>
      <div class="row"><span>Utilisateur</span><span>${info.user || "kemus"}</span></div>
      <div class="row"><span>Hote</span><span>${info.host || "—"}</span></div>
      <div class="row"><span>Port serveur</span><span>${info.port || 3922}</span></div>
      <div class="row"><span>Theme</span><span>Breeze-Dark + NixSnow</span></div>
      <div class="row"><span>Backend</span><span>Python stdlib</span></div>
      <div class="row"><span>VFS racine</span><span>~/.kemus/home</span></div>
      <h2 style="margin-top:14px">A propos</h2>
      <p style="color:#9aa4b8;font-size:12px;line-height:1.6">
        Kemus est un PC virtuel leger, sans dependance externe, inspire de KDE Plasma,
        NixOS et Fedora. Tout le code tient dans un seul fichier Python.
      </p>
    </div>`;
}

// ====== Help ======
function openHelp() {
  const { id, body } = createWindow({ title: "Aide Kemus", width: 480, height: 380 });
  body.innerHTML = `
    <div class="help">
      <h2>Bienvenue dans Kemus</h2>
      <p>PC virtuel leger, inspire KDE Plasma + NixOS + Fedora.</p>
      <h3 style="margin-top:12px;color:#3daee9">Raccourcis Terminal</h3>
      <ul>
        <li><code>help</code> — liste des commandes</li>
        <li><code>neofetch</code> — info systeme</li>
        <li><code>ls</code>, <code>cd</code>, <code>cat</code>, <code>mkdir</code>, <code>touch</code>, <code>rm</code></li>
        <li><code>edit &lt;file&gt;</code> — ouvrir dans l'editeur</li>
        <li><code>python -c "print(1+1)"</code> — executer du Python</li>
        <li><code>apt install firefox</code> — simulation gestionnaire</li>
        <li><code>Ctrl+L</code> — effacer ; <code>Ctrl+C</code> — interrompre</li>
      </ul>
      <h3 style="margin-top:12px;color:#3daee9">Fenetres</h3>
      <ul>
        <li>Glisser la barre de titre pour deplacer</li>
        <li>Corner bas-droite pour redimensionner</li>
        <li>Boutons — / ▢ / ✕ pour reduire / agrandir / fermer</li>
      </ul>
    </div>`;
}

// ====== Browser (mock) ======
function openBrowser() {
  const { id, body } = createWindow({ title: "Navigateur", width: 700, height: 460 });
  body.innerHTML = `
    <div class="browser">
      <div class="browser-toolbar">
        <button>←</button><button>→</button><button>⟳</button>
        <input id="br-url" value="kemus://welcome" placeholder="URL ou recherche">
        <button id="br-go">Go</button>
      </div>
      <iframe class="browser-frame" id="br-frame" sandbox></iframe>
    </div>`;
  const frame = body.querySelector("#br-frame");
  const go = () => {
    const u = body.querySelector("#br-url").value;
    frame.srcdoc = `<!DOCTYPE html><html><body style="font-family:sans-serif;padding:20px;background:#fff;color:#000">
      <h1>Bienvenue dans KemusBrowser</h1>
      <p>URL demandee : <code>${u}</code></p>
      <p>Ceci est un navigateur simule. Pour un vrai navigateur, utilisez Firefox.</p>
      <p>Le PC virtuel Kemus tourne sur le port 3922.</p>
    </body></html>`;
  };
  body.querySelector("#br-go").onclick = go;
  body.querySelector("#br-url").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  go();
}

// ====== Calculator ======
function openCalc() {
  const { id, body } = createWindow({ title: "Calculatrice", width: 240, height: 320 });
  body.innerHTML = `
    <div class="calc">
      <div class="calc-display" id="calc-disp">0</div>
      <div class="calc-grid">
        <button class="op" data-k="C">C</button><button class="op" data-k="(">(</button><button class="op" data-k=")">)</button><button class="op" data-k="/">/</button>
        <button data-k="7">7</button><button data-k="8">8</button><button data-k="9">9</button><button class="op" data-k="*">*</button>
        <button data-k="4">4</button><button data-k="5">5</button><button data-k="6">6</button><button class="op" data-k="-">-</button>
        <button data-k="1">1</button><button data-k="2">2</button><button data-k="3">3</button><button class="op" data-k="+">+</button>
        <button data-k="0">0</button><button data-k=".">.</button><button data-k="back">⌫</button><button class="eq" data-k="=">=</button>
      </div>
    </div>`;
  const disp = body.querySelector("#calc-disp");
  let expr = "";
  body.querySelectorAll(".calc-grid button").forEach(btn => {
    btn.onclick = () => {
      const k = btn.dataset.k;
      if (k === "C") { expr = ""; disp.textContent = "0"; }
      else if (k === "back") { expr = expr.slice(0, -1); disp.textContent = expr || "0"; }
      else if (k === "=") {
        try {
          // Safe-ish eval: only digits and operators
          if (!/^[0-9+\-*/.() ]+$/.test(expr)) throw 0;
          const r = Function('"use strict";return (' + expr + ')')();
          disp.textContent = String(r); expr = String(r);
        } catch (e) { disp.textContent = "Erreur"; expr = ""; }
      } else { expr += k; disp.textContent = expr; }
    };
  });
}

// ====== Kickoff menu ======
function buildKickoff() {
  const grid = document.getElementById("km-grid");
  grid.innerHTML = "";
  Object.entries(APPS).forEach(([key, app]) => {
    const div = document.createElement("div");
    div.className = "km-app";
    div.innerHTML = app.icon + '<span>' + app.name + '</span>';
    div.onclick = () => { app.open(); closeKickoff(); };
    grid.appendChild(div);
  });
  const search = document.getElementById("km-search-input");
  search.oninput = () => {
    const q = search.value.toLowerCase();
    grid.innerHTML = "";
    Object.entries(APPS).forEach(([key, app]) => {
      if (app.name.toLowerCase().includes(q)) {
        const div = document.createElement("div");
        div.className = "km-app";
        div.innerHTML = app.icon + '<span>' + app.name + '</span>';
        div.onclick = () => { app.open(); closeKickoff(); };
        grid.appendChild(div);
      }
    });
  };
}

function closeKickoff() {
  document.getElementById("kickoff-menu").classList.remove("open");
}

document.getElementById("kickoff").onclick = (e) => {
  e.stopPropagation();
  document.getElementById("kickoff-menu").classList.toggle("open");
  if (document.getElementById("kickoff-menu").classList.contains("open")) {
    document.getElementById("km-search-input").focus();
  }
};

document.addEventListener("click", (e) => {
  if (!e.target.closest("#kickoff-menu") && !e.target.closest("#kickoff")) {
    closeKickoff();
  }
});

// ====== Dock ======
function buildDock() {
  const dock = document.getElementById("dock");
  const items = ["terminal", "files", "editor", "browser", "calc", "settings", "help"];
  items.forEach(key => {
    const app = APPS[key];
    const div = document.createElement("div");
    div.className = "dock-icon";
    div.innerHTML = app.icon;
    div.title = app.name;
    div.onclick = () => app.open();
    dock.appendChild(div);
  });
}

// ====== Desktop icons ======
function buildDesktopIcons() {
  const wrap = document.getElementById("desktop-icons");
  const items = [
    { key: "terminal", label: "Terminal" },
    { key: "files", label: "Fichiers" },
    { key: "editor", label: "Editeur" },
    { key: "help", label: "Aide" },
  ];
  items.forEach(it => {
    const div = document.createElement("div");
    div.className = "desk-icon";
    div.innerHTML = APPS[it.key].icon + '<span>' + it.label + '</span>';
    div.ondblclick = () => APPS[it.key].open();
    div.onclick = (e) => { if (e.detail === 2) APPS[it.key].open(); };
    wrap.appendChild(div);
  });
}

// ====== Clock ======
function tickClock() {
  const now = new Date();
  const d = now.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "short" });
  const t = now.toLocaleTimeString("fr-FR", { hour12: false });
  document.getElementById("clock").textContent = d + " " + t;
}
setInterval(tickClock, 1000); tickClock();

// ====== Boot sequence ======
async function boot() {
  const bar = document.getElementById("boot-bar");
  const txt = document.getElementById("boot-text");
  const steps = ["Initialisation du noyau...", "Chargement des modules Plasma...", "Montage du VFS ~/.kemus/home...", "Demarrage du serveur port 3922...", "Connexion a la session...", "Pret."];
  for (let i = 0; i < steps.length; i++) {
    txt.textContent = steps[i];
    bar.style.width = ((i + 1) / steps.length * 100) + "%";
    await new Promise(r => setTimeout(r, 280));
  }
  await new Promise(r => setTimeout(r, 300));
  document.getElementById("boot").style.display = "none";
  buildKickoff();
  buildDock();
  buildDesktopIcons();
  // Auto-open terminal
  setTimeout(() => openTerminal(), 400);
}

boot();
</script>
</body>
</html>"""


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def find_free_port(preferred: int) -> int:
    """Try preferred port; if taken, walk up to 3932 etc."""
    for port in [preferred, preferred + 1, preferred + 2, preferred + 10, 0]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((HOST, port))
                return port if port != 0 else s.getsockname()[1]
        except OSError:
            continue
    return 0


def main():
    seed_vfs()
    port = find_free_port(PORT)
    if port != PORT:
        print(f"[Kemus] Port {PORT} occupe, utilisation de {port}", file=sys.stderr)
    server = ThreadingHTTPServer((HOST, port), KemusHandler)
    daemon = server.serve_forever
    url = f"http://{HOST}:{port}/"
    banner = f"""
============================================================
  Kemus {VERSION} - PC virtuel leger (KDE/NixOS/Fedora)
  VFS reel : {VFS_ROOT}
  Log      : {LOG_FILE}
  URL      : {url}
============================================================
  Ouvrez {url} dans Firefox / Chrome.
  Ctrl+C pour arreter.
============================================================
"""
    print(banner)
    try:
        daemon()
    except KeyboardInterrupt:
        print("\n[Kemus] Arret...")
        server.shutdown()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Review a module submitted for the index -- stage 1, where none of its code runs.

Usage::

    PYTHONPATH=src python3.12 dev/tools/review_module.py <author repo> --tag v3 \\
        --asset https://github.com/<author>/<repo>/releases/download/v3/weather-3.zip \\
        --author-key author.pub --author-sig <base64> [--index index.json]

Nothing is listed without the maintainer reading the source at the release's
tagged commit, every version (the trust design, memory
project_modules_trust_design).  This tool does not decide; it points.  It
fetches the author's repository (the repository root is the module folder),
and reports:

* **Provenance** -- the tag's commit, packed again (``module_bundle.py``), is
  byte for byte the bundle on the release, and the author signed it
  (``ModuleTrust.check_release``, the app's own check).
* **Continuity** -- with ``--index``: the commit last approved is in this
  one's history, and the author key is the one listed.  The DIFF since that
  commit is what to read; a malicious update hides in an update.
* **Layout** -- the manifest reads, only a module's fixed names are packed,
  no symbolic link, no compiled or binary file.
* **Code** -- from the syntax tree, never by running it.  Hard: a library
  TRCC does not let a module use, any network use (there is no network
  capability yet), ``exec``/``eval``/``compile``, an import by computed name.
  To read: running programs, native code, deleting or writing files,
  pickle/marshal, long encoded literals.
* **The bar TRCC's own code meets** -- module boundaries (``layers``), a
  log line in every function (``logging_coverage``), every word in every
  language (``i18n_gaps``), ruff and pyright clean in a copy of this tree.
* **For judging KISS/SOLID/DRY** -- heaviest functions
  (``function_census``) and bodies copied within the module or from TRCC.

Stage 2 -- its own tests and ``dev/smoke_modules.py``, which DO run its code --
is not built yet; until it is, a module that passes here is not listable.

Untrusted input: everything fetched goes into a new, empty folder; nothing
from it is imported or run, and ruff and pyright read a copy of only the
packed files inside a ``git worktree`` of this checkout.

Exit status: 0 when every stage-1 gate passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import function_census
import i18n_gaps
import layers
import logging_coverage
import module_bundle
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_public_key,
)

from trcc.adapters.modules import Ed25519Checker, ManifestError, read_manifest
from trcc.core.errors import UntrustedModule
from trcc.services.module_trust import ModuleTrust, parse_release

_REPO = Path(__file__).resolve().parents[2]

#: Libraries beyond the stdlib a module may import: what TRCC ships and its
#: own two modules use (measured 2026-10-23).  psutil, pyusb and hid ship too
#: but are left out -- hardware is reached through the host's port.
ALLOWED_LIBRARIES = frozenset({"numpy", "PySide6", "fastapi", "pydantic", "typer"})
#: Network libraries: a hard stop -- a module reaches the internet only
#: through ``host.network``, which holds it to the sites it names.
NETWORK = frozenset({"socket", "ssl", "http", "urllib", "urllib3", "ftplib",
                     "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc",
                     "socketserver", "requests", "httpx", "aiohttp", "websockets"})
#: Imports to read closely, and why.
READ_IMPORTS = {
    "subprocess": "runs programs", "pty": "runs programs",
    "multiprocessing": "starts processes", "ctypes": "calls native code",
    "cffi": "calls native code", "pickle": "loads data that can carry code",
    "marshal": "loads data that can carry code", "shelve": "loads data that can carry code",
    "importlib": "imports by name", "shutil": "moves or deletes files",
}
#: Calls that run text as code.
HARD_CALLS = frozenset({"exec", "eval", "compile", "__import__"})
#: ``os.<name>`` calls to read closely.
READ_OS_CALLS = frozenset({"system", "popen", "fork", "forkpty", "kill", "remove",
                           "unlink", "rmdir", "removedirs", "execv", "execve",
                           "execl", "execlp", "execvp", "spawnv", "spawnl"})
COMPILED = frozenset({".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib", ".exe",
                      ".bin", ".whl", ".zip", ".egg", ".jar"})
#: Files that are binary by nature.
MEDIA = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".ttf", ".otf"})
#: A literal this long with no whitespace in it is data, not words.
BLOB_CHARS = 200
#: A function body this many statements long, found twice, is a copy.
COPY_STATEMENTS = 6
#: The largest release asset fetched.
MAX_ASSET = 64 << 20


@dataclass(frozen=True)
class Finding:
    """One thing the review saw: *hard* ones keep a module out of the index."""
    where: str
    text: str
    hard: bool = True


@dataclass
class Gate:
    """One check, and what it found."""
    name: str
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.hard for f in self.findings)

    def fail(self, where: str, text: str) -> None:
        self.findings.append(Finding(where, text))

    def read(self, where: str, text: str) -> None:
        self.findings.append(Finding(where, text, hard=False))


@dataclass
class Review:
    """Everything the report shows."""
    key: str
    version: str
    commit: str
    gates: list[Gate]
    diff: str
    since: str
    capabilities: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()
    #: Whether stage 2 -- its own code, in the sandbox -- ran.
    ran: bool = False

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates)


# ── fetching ──────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    """Run git with no prompt and no hook of the fetched repository's."""
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], capture_output=True,
        text=True, check=False, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode != 0:
        raise SystemExit(f"STOP: git {' '.join(args[:2])}: {result.stderr.strip()}")
    return result.stdout


def fetch(repo: str, tag: str, into: Path) -> tuple[Path, str]:
    """*repo* cloned into the empty folder *into*, checked out at *tag*:
    the folder and the tag's commit."""
    clone = into / "repo"
    _git("clone", "--quiet", "--no-checkout", "--", repo, str(clone))
    commit = _git("-C", str(clone), "rev-parse", "--verify",
                  f"refs/tags/{tag}^{{commit}}").strip()
    _git("-C", str(clone), "-c", "advice.detachedHead=false", "checkout",
         "--quiet", commit)
    return clone, commit


def read_asset(asset: str) -> bytes:
    """The release bundle: a local file, or an https URL read up to
    ``MAX_ASSET`` bytes."""
    if not asset.startswith("https://"):
        return Path(asset).read_bytes()
    with urllib.request.urlopen(asset, timeout=60) as response:
        data = response.read(MAX_ASSET + 1)
    if len(data) > MAX_ASSET:
        raise SystemExit(f"STOP: {asset} is over {MAX_ASSET} bytes")
    return data


def raw_key(pub_file: Path) -> str:
    """The raw Ed25519 public key in PEM *pub_file*, base64."""
    key = load_pem_public_key(pub_file.read_bytes())
    return base64.b64encode(key.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


# ── the gates ─────────────────────────────────────────────────────────────


def provenance(key: str, version: str, commit: str, asset: bytes | None,
               rebuilt: bytes, author_key: str | None, author_sig: str | None,
               pinned_author: str | None) -> Gate:
    """The release asset is what the commit packs to, signed by its author."""
    gate = Gate("Provenance")
    gate.notes.append(f"tag -> commit {commit}")
    gate.notes.append(f"packed from the commit: {len(rebuilt)} bytes, "
                      f"sha256 {hashlib.sha256(rebuilt).hexdigest()}")
    if asset is None:
        gate.fail("release", "no release asset given (--asset)")
        return gate
    if asset != rebuilt:
        gate.fail("release", f"the release asset ({len(asset)} bytes, sha256 "
                  f"{hashlib.sha256(asset).hexdigest()}) is not what the tagged "
                  "commit packs to -- it was not built from the reviewed source")
        return gate
    if author_key is None or author_sig is None:
        gate.fail("signature", "no author signature given (--author-key, --author-sig)")
        return gate
    entry = {"version": version, "url": "https://review.invalid/", "size": len(asset),
             "sha256": hashlib.sha256(asset).hexdigest(), "min_trcc": "-",
             "commit": commit, "capabilities": [], "author_key": author_key,
             "author_sig": author_sig}
    try:
        ModuleTrust(Ed25519Checker(), []).check_release(
            parse_release(key, entry), asset, pinned_author)
    except UntrustedModule as e:
        gate.fail("signature", str(e))
    return gate


def continuity(clone: Path, since: str, commit: str, version: str = "",
               approved: str = "") -> tuple[Gate, str]:
    """The commit last approved is an ancestor of this one, and *version* is
    not the *approved* release's; the diff since."""
    gate = Gate("Continuity")
    if approved and approved == version:
        gate.fail("module.json", f"version {version} is the approved release's -- "
                  "an update needs a new one, or no machine can tell them apart")
    paths = sorted(module_bundle.BUNDLE_FILES | module_bundle.BUNDLE_DIRS)
    if not since:
        gate.notes.append("first submission -- read ALL of it")
        listing = _git("-C", str(clone), "ls-files", "--", *paths)
        return gate, listing
    ancestor = subprocess.run(
        ["git", "-C", str(clone), "merge-base", "--is-ancestor", since, commit],
        capture_output=True, check=False)
    if ancestor.returncode != 0:
        gate.fail("history", f"the approved commit {since} is not in this release's "
                  "history -- rewritten, so the diff cannot be trusted; review it whole")
        return gate, _git("-C", str(clone), "ls-files", "--", *paths)
    gate.notes.append(f"since the approved commit {since}")
    gate.notes.append(_git("-C", str(clone), "diff", "--stat", since, commit,
                           "--", *paths).strip() or "(no change to the module)")
    return gate, _git("-C", str(clone), "diff", since, commit, "--", *paths)


def layout(source: Path, staged: Path) -> Gate:
    """The manifest reads; its key is free; only a module's files; nothing
    compiled."""
    gate = Gate("Layout")
    if (_REPO / "src" / "trcc" / "modules" / staged.name).exists():
        gate.fail("module.json", f"key {staged.name!r} is a built-in module's")
    try:
        manifest = read_manifest(staged / "module.json")
    except ManifestError as e:
        gate.fail("module.json", str(e))
    else:
        gate.notes.append(f"{manifest.key} {manifest.version}: {manifest.title}; "
                          f"capabilities {', '.join(manifest.capabilities) or 'none'}")
        if manifest.network_hosts:
            gate.notes.append(f"fetches from: {', '.join(manifest.network_hosts)}")
    if extra := module_bundle.left_out(source):
        gate.notes.append(f"not packed (not part of a module): {', '.join(extra)}")
    for path in sorted(p for p in staged.rglob("*") if p.is_file()):
        rel = path.relative_to(staged).as_posix()
        if path.suffix.lower() in COMPILED:
            gate.fail(rel, "a compiled or packed file -- only source can be reviewed")
        elif path.suffix != ".py" and path.suffix.lower() not in MEDIA \
                and b"\0" in path.read_bytes():
            gate.fail(rel, "binary data in a file that should be text")
    return gate


class _Scanner(ast.NodeVisitor):
    """The code flags of one file, from its syntax tree."""

    def __init__(self, gate: Gate, rel: str) -> None:
        self._gate, self._rel = gate, rel

    def _at(self, node: ast.AST) -> str:
        return f"{self._rel}:{getattr(node, 'lineno', 0)}"

    def _imported(self, node: ast.AST, name: str) -> None:
        top = name.split(".")[0]
        if top == "trcc":
            return                       # the boundary gate's
        if top in NETWORK:
            self._gate.fail(self._at(node), f"imports {name}: network -- a module "
                            "fetches through host.network, from the sites it names")
        elif top in READ_IMPORTS:
            self._gate.read(self._at(node), f"imports {name}: {READ_IMPORTS[top]}")
        elif top not in sys.stdlib_module_names and top not in ALLOWED_LIBRARIES:
            self._gate.fail(self._at(node), f"imports {name}: not a library a "
                            "module may use")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._imported(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self._imported(node, node.module)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        literal = bool(node.args) and isinstance(node.args[0], ast.Constant)
        if isinstance(func, ast.Name) and func.id in HARD_CALLS:
            self._gate.fail(self._at(node), f"{func.id}() -- runs text as code")
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else ""
            if func.attr in ("import_module", "__import__"):
                if literal:
                    self._gate.read(self._at(node), f"{func.attr}() of a fixed name")
                else:
                    self._gate.fail(self._at(node), f"{func.attr}() of a computed name")
            elif owner == "os" and func.attr in READ_OS_CALLS:
                self._gate.read(self._at(node), f"os.{func.attr}()")
            elif func.attr in ("write_text", "write_bytes", "rmtree", "unlink"):
                self._gate.read(self._at(node), f".{func.attr}() -- changes files")
        if isinstance(func, ast.Name) and func.id == "open":
            mode = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "mode"), None)
            if mode is not None and not (isinstance(mode, ast.Constant)
                                         and not set(str(mode.value)) & set("wax+")):
                self._gate.read(self._at(node), "open() for writing")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if isinstance(value, (str, bytes)) and len(value) > BLOB_CHARS and not any(
                chr(c).isspace() if isinstance(c, int) else c.isspace() for c in value):
            self._gate.read(self._at(node), f"a {len(value)}-character literal with "
                            "no spaces -- encoded data?")


def code(staged: Path) -> Gate:
    """The code flags of every packed ``.py`` file."""
    gate = Gate("Code")
    for path in sorted(staged.rglob("*.py")):
        rel = path.relative_to(staged).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), rel)
        except (SyntaxError, UnicodeDecodeError) as e:
            gate.fail(rel, f"does not parse ({e})")
            continue
        _Scanner(gate, rel).visit(tree)
    return gate


def boundaries(root: Path) -> Gate:
    """Imports only TRCC's core, ui and module SDK -- ``layers``'s rule."""
    gate = Gate("Module boundaries")
    for breach in layers.module_boundary_breaches(root):
        where, _, text = breach.strip().partition(" -> ")
        gate.fail(where, text)
    return gate


def logging_lines(staged: Path) -> Gate:
    """Every function logs -- TRCC's rule, with no ratchet for a new module."""
    gate = Gate("A log line in every function")
    for name in logging_coverage.silent_functions(staged):
        gate.fail(name, "no log line")
    return gate


def words(modules: Path, key: str) -> Gate:
    """Its ``i18n.json`` has every word in every language."""
    gate = Gate("Every word in every language")
    table = i18n_gaps.tables(modules).get(key)
    if table is None:
        gate.fail("i18n.json", "missing")
        return gate
    for lang, missing in sorted(i18n_gaps.gaps(table).items()):
        gate.fail(f"i18n.json {lang}", f"{len(missing)} word(s) missing: "
                  f"{', '.join(missing[:3])}{' ...' if len(missing) > 3 else ''}")
    return gate


@dataclass
class _Tree:
    """A ``git worktree`` of this checkout's HEAD with the module in it."""
    path: Path

    def run(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(command), cwd=self.path, capture_output=True,
                              text=True, check=False)


def lint(tree: _Tree, key: str) -> list[Gate]:
    """ruff and pyright, with this tree's settings, on the module alone."""
    folder = f"src/trcc/modules/{key}"
    ruff = Gate("ruff")
    result = tree.run("ruff", "check", "--output-format", "concise", folder)
    for line in result.stdout.splitlines():
        if line.startswith(folder):
            ruff.fail(line.split(": ", 1)[0], line.split(": ", 1)[-1])
    if result.returncode not in (0, 1):
        ruff.fail("ruff", result.stderr.strip() or f"exit {result.returncode}")
    pyright = Gate("pyright")
    result = tree.run("pyright", "--outputjson", folder)
    try:
        diagnostics = json.loads(result.stdout)["generalDiagnostics"]
    except (ValueError, KeyError):
        pyright.fail("pyright", result.stderr.strip() or "no report")
        diagnostics = []
    for d in diagnostics:
        if d.get("severity") in ("error", "warning"):
            where = Path(d["file"]).relative_to(tree.path / folder).as_posix()
            pyright.fail(f"{where}:{d['range']['start']['line'] + 1}",
                         f"{d['severity']}: {d['message'].splitlines()[0]}")
    return [ruff, pyright]


def _bodies(paths: Iterator[Path]) -> Iterator[tuple[str, str]]:
    """``(the body's shape, where)`` of every function long enough to count."""
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body[1:] if ast.get_docstring(node) else node.body
                if len(body) >= COPY_STATEMENTS:
                    shape = ast.dump(ast.Module(body=body, type_ignores=[]))
                    yield shape, f"{path}:{node.lineno} {node.name}"


def judgement(staged: Path, key: str) -> Gate:
    """For the maintainer's KISS/SOLID/DRY call: weights and copies."""
    gate = Gate("For your judgement (KISS / SOLID / DRY)")
    rows = function_census.census(staged)
    for row in rows[:5]:
        gate.notes.append(f"{row.path}:{row.lineno} {row.name}: {row.lines} lines, "
                          f"{row.decisions} decisions")
    for row in rows:
        if (row.lines > function_census.LONG_FUNCTION_LINES
                or row.decisions >= function_census.BRANCHY_FUNCTION_DECISIONS):
            gate.read(f"{row.path}:{row.lineno}", f"{row.name}: {row.lines} lines, "
                      f"{row.decisions} decisions -- over TRCC's limit")
    trcc = _REPO / "src" / "trcc"
    theirs: dict[str, str] = {}
    for shape, where in _bodies(iter(sorted(staged.rglob("*.py")))):
        here = where.replace(f"{staged}/", "")
        if shape in theirs:
            gate.read(here, f"the same body as {theirs[shape]}")
        theirs.setdefault(shape, here)
    ours = (p for p in sorted(trcc.rglob("*.py"))
            if not p.is_relative_to(trcc / "modules" / key))
    for shape, where in _bodies(ours):
        if shape in theirs:
            gate.read(theirs[shape], "the same body as TRCC's "
                      + where.replace(f"{_REPO}/", ""))
    return gate


# ── stage 2: its code runs, in the sandbox ────────────────────────────────

CONTAINERFILE = _REPO / "dev" / "review" / "Containerfile"
#: The longest one sandboxed run may take.
STAGE_TWO_SECONDS = 900
#: Where the tree under review sits inside the sandbox.
INSIDE = "/review"
#: The interpreter in the image, with TRCC's dependencies.
PYTHON = "/venv/bin/python"

#: Run inside the sandbox before anything of the author's: each must say True,
#: or stage 2 does not run.  Argument 1 is this machine's home, 2 its runtime
#: directory -- where the running App's socket is.
PROBE = """
import json, os, socket, sys
from pathlib import Path

def refused(attempt):
    try:
        attempt()
    except OSError:
        return True
    return False

caps = Path("/proc/self/status").read_text().split("CapEff:")[1].split()[0]
print(json.dumps({
    "no network": refused(lambda: socket.create_connection(("1.1.1.1", 443), timeout=3)),
    "no name lookup": refused(lambda: socket.getaddrinfo("github.com", 443)),
    "tree read-only": refused(lambda: Path("/review/.probe").write_text("x")),
    "no USB devices": not Path("/dev/bus/usb").exists(),
    "no host home": not Path(sys.argv[1]).exists(),
    "no host runtime dir": not Path(sys.argv[2]).exists(),
    "not root": os.geteuid() != 0,
    "no capabilities": int(caps, 16) == 0,
}))
"""


def image_tag() -> str:
    """The sandbox image for this Containerfile and these dependencies."""
    digest = hashlib.sha256(CONTAINERFILE.read_bytes()
                            + (_REPO / "pyproject.toml").read_bytes()).hexdigest()
    return f"localhost/trcc-review:{digest[:12]}"


def ensure_image(workdir: Path) -> str:
    """The sandbox image, built first if this machine lacks it -- the one
    step with network.  Built from a folder holding only its two inputs."""
    tag = image_tag()
    if subprocess.run(["podman", "image", "exists", tag], check=False).returncode == 0:
        return tag
    context = workdir / "image"
    context.mkdir()
    for source in (CONTAINERFILE, _REPO / "pyproject.toml"):
        shutil.copyfile(source, context / source.name)
    print(f"Building {tag} -- once, with network; this takes minutes ...")
    subprocess.run(["podman", "build", "-t", tag, "-f", "Containerfile", "."],
                   cwd=context, check=True)
    return tag


def sandbox(image: str, tree: Path, *command: str) -> list[str]:
    """The podman command that runs *command* on *tree* in *image*: no
    network, no device, no capability, no privilege to gain; the tree
    read-only and a throwaway /tmp and home; capped in memory, processes
    and time."""
    return ["podman", "run", "--rm", "--network=none", "--cap-drop=all",
            "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs=/tmp:rw,size=1g,mode=1777",
            "--tmpfs=/home/ubuntu:rw,size=256m,mode=1777",
            "--memory=4g", "--pids-limit=1024", f"--timeout={STAGE_TWO_SECONDS}",
            "--env=HOME=/home/ubuntu", "--env=PYTHONDONTWRITEBYTECODE=1",
            f"--env=PYTHONPATH={INSIDE}/src", "--env=QT_QPA_PLATFORM=offscreen",
            f"--volume={tree}:{INSIDE}:ro,Z", f"--workdir={INSIDE}", image, *command]


def _contained(workdir: Path, image: str, tree: Path,
               *command: str) -> subprocess.CompletedProcess[str]:
    """*command* in the sandbox, started from *workdir* -- never from the
    repo (SELinux asks about a container started there)."""
    return subprocess.run(sandbox(image, tree, *command), cwd=workdir,
                          capture_output=True, text=True, check=False,
                          timeout=STAGE_TWO_SECONDS + 60)


def proven(workdir: Path, image: str, tree: Path) -> Gate:
    """The sandbox refuses what it must, measured inside it."""
    gate = Gate("Sandbox")
    result = _contained(workdir, image, tree, PYTHON, "-I", "-c", PROBE,
                        str(Path.home()), os.environ.get("XDG_RUNTIME_DIR", "/run/user"))
    try:
        probes = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        gate.fail("probe", f"did not run: {(result.stderr or result.stdout)[-300:]}")
        return gate
    for name, held in probes.items():
        if held:
            gate.notes.append(f"{name}: held")
        else:
            gate.fail(name, "NOT held inside the sandbox")
    return gate


def export(workdir: Path, staged: Path, key: str, tests: Path) -> Path:
    """This checkout's HEAD with no ``.git``, the module in it as a built-in
    is, and its tests under ``tests/module_<key>/`` -- where TRCC's own
    ``conftest.py`` gives them its fakes and its HOME guard."""
    tree = workdir / "export"
    tree.mkdir()
    archive = subprocess.run(["git", "-C", str(_REPO), "archive", "--format=tar",
                              "HEAD"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    shutil.copytree(staged, tree / "src" / "trcc" / "modules" / key)
    # dev/_mock_bootstrap.py makes these on IMPORT (the smoke imports it), and
    # git keeps none of them: present, the read-only tree still refuses any
    # write into them.
    for folder in (".trcc/data", ".trcc-user"):
        (tree / "dev" / folder).mkdir(parents=True, exist_ok=True)
    if tests.is_dir():
        into = tree / "tests" / f"module_{key}"
        shutil.copytree(tests, into, symlinks=True)
        (into / "__init__.py").touch()
    return tree


def _log(workdir: Path, name: str, result: subprocess.CompletedProcess[str]) -> Path:
    path = workdir / f"{name}.log"
    path.write_text(f"exit {result.returncode}\n{result.stdout}\n{result.stderr}",
                    encoding="utf-8")
    return path


def own_tests(workdir: Path, image: str, tree: Path, key: str) -> Gate:
    """Its ``tests/`` pass, in the sandbox -- and there are some."""
    gate = Gate("Its own tests")
    if not (tree / "tests" / f"module_{key}").is_dir():
        gate.fail("tests/", "no tests -- a module ships its own")
        return gate
    result = _contained(workdir, image, tree, PYTHON, "-m", "pytest",
                        f"tests/module_{key}", "-q", "-n", "0", "-p", "no:cacheprovider")
    log = _log(workdir, "own-tests", result)
    tail = (result.stdout.strip().splitlines() or [""])[-1]
    gate.notes.append(f"{tail}  (full output: {log})")
    if result.returncode == 5:
        gate.fail("tests/", "no test was collected")
    elif result.returncode != 0:
        gate.fail("tests/", f"failed, exit {result.returncode}")
    return gate


def smoke(workdir: Path, image: str, tree: Path, key: str) -> Gate:
    """``dev/smoke_modules.py --only <key>``: every UI and a real daemon, on
    the mock panel, in the sandbox."""
    gate = Gate("Smoke (every UI and a daemon, on the mock)")
    result = _contained(workdir, image, tree, PYTHON, "dev/smoke_modules.py",
                        "--only", key)
    log = _log(workdir, "smoke", result)
    lines = result.stdout.splitlines()
    gate.notes += [line.strip() for line in lines if "Summary:" in line]
    gate.notes.append(f"full output: {log}")
    for line in lines:
        if "[FAIL]" in line:
            gate.fail("smoke", line.strip().removeprefix("[FAIL]").strip())
    if result.returncode != 0 and gate.passed:
        gate.fail("smoke", f"exit {result.returncode}: {(result.stderr or '')[-300:]}")
    return gate


def stage_two(result: Review, workdir: Path) -> None:
    """Run the module's own code in the sandbox -- once the sandbox has
    proven itself -- and add what it found to *result*."""
    source = workdir / "repo"
    staged = workdir / "staged" / "trcc" / "modules" / result.key
    image = ensure_image(workdir)
    tree = export(workdir, staged, result.key, source / "tests")
    sandbox_gate = proven(workdir, image, tree)
    result.gates.append(sandbox_gate)
    result.ran = True
    if not sandbox_gate.passed:
        return
    result.gates += [own_tests(workdir, image, tree, result.key),
                     smoke(workdir, image, tree, result.key)]


# ── the review ────────────────────────────────────────────────────────────


def review(repo: str, tag: str, workdir: Path, asset: bytes | None = None,
           author_key: str | None = None, author_sig: str | None = None,
           index: Path | None = None) -> Review:
    """Stage 1 of reviewing *repo* at *tag*, working in the empty *workdir*."""
    source, commit = fetch(repo, tag, workdir)
    try:
        rebuilt = module_bundle.pack(source)
        key = module_bundle.module_key(source)
    except module_bundle.BundleError as e:
        gate = Gate("Layout")
        gate.fail("bundle", str(e))
        return Review("?", "?", commit, [gate], "", "")
    root = workdir / "staged"
    modules = root / "trcc" / "modules"
    staged = modules / key
    for rel in module_bundle.members(source):
        (staged / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, staged / rel)
    version = str(json.loads((staged / "module.json").read_text(encoding="utf-8"))
                  .get("version", "0"))
    since, pinned, approved = "", None, ""
    if index is not None:
        listed = json.loads(index.read_text(encoding="utf-8"))["signed"]["modules"].get(key)
        if listed is not None:
            since, pinned = listed["commit"], listed["author_key"]
            approved = str(listed.get("version", ""))
    history, diff = continuity(source, since, commit, version, approved)
    gates = [provenance(key, version, commit, asset, rebuilt, author_key,
                        author_sig, pinned), history, layout(source, staged),
             code(staged), boundaries(root), logging_lines(staged),
             words(modules, key)]
    tree = _Tree(workdir / "tree")
    _git("-C", str(_REPO), "worktree", "add", "--quiet", "--detach", str(tree.path), "HEAD")
    try:
        into = tree.path / "src" / "trcc" / "modules" / key
        shutil.rmtree(into, ignore_errors=True)    # a key taken by a built-in
        shutil.copytree(staged, into)
        gates += lint(tree, key)
    finally:
        _git("-C", str(_REPO), "worktree", "remove", "--force", str(tree.path))
    gates.append(judgement(staged, key))
    try:
        manifest = read_manifest(staged / "module.json")
    except ManifestError:
        return Review(key, version, commit, gates, diff, since)
    return Review(key, version, commit, gates, diff, since, manifest.capabilities,
                  manifest.network_hosts)


def render(result: Review, diff_file: Path) -> str:
    """The report, as Markdown."""
    ran = ("stages 1 and 2 -- its own code ran only in the sandbox" if result.ran
           else "stage 1 -- none of its code was run")
    lines = [f"# Review: {result.key} {result.version}", "",
             f"Commit `{result.commit}` -- {ran}.", ""]
    for gate in result.gates:
        hard = [f for f in gate.findings if f.hard]
        status = "PASS" if gate.passed else f"FAIL ({len(hard)})"
        lines += [f"## {gate.name}: {status}", ""]
        lines += [f"- {note}" for note in gate.notes]
        lines += [f"- **{f.where}**: {f.text}" for f in hard]
        lines += [f"- read: {f.where}: {f.text}" for f in gate.findings if not f.hard]
        lines.append("")
    lines += [f"The {'diff' if result.since else 'file list'} to read: `{diff_file}`", ""]
    if not result.passed:
        lines += ["## Verdict: NOT listable -- see each FAIL above"]
    elif not result.ran:
        lines += ["## Verdict: stage 1 passed", "",
                  "NOT listable yet: read this report and the diff, then run again "
                  "with --run for stage 2 (its tests and smoke_modules, in the sandbox)."]
    else:
        lines += ["## Verdict: listable -- if your read agrees", "",
                  "Both stages passed.  The decision is yours; to list it:", "",
                  "```",
                  f"sign_module_index.py add index.json --root root.json --module "
                  f"{result.key} --version {result.version} --commit {result.commit} "
                  f"--capabilities {','.join(result.capabilities)} "
                  + (f"--network-hosts {','.join(result.network_hosts)} "
                     if result.network_hosts else "")
                  + "--bundle <asset> "
                  "--url <asset url> --min-trcc <version> --author-key <pub> "
                  "--author-sig <sig>", "```"]
    return "\n".join(lines) + "\n"


def _selftest_sandbox() -> int:
    """``--selftest-sandbox``: the probes alone, on an export of HEAD."""
    workdir = Path(tempfile.mkdtemp(prefix="trcc-review-"))
    image = ensure_image(workdir)
    tree = workdir / "export"
    tree.mkdir()
    archive = subprocess.run(["git", "-C", str(_REPO), "archive", "--format=tar",
                              "HEAD"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    gate = proven(workdir, image, tree)
    for note in gate.notes:
        print(f"  ok    {note}")
    for finding in gate.findings:
        print(f"  FAIL  {finding.where}: {finding.text}")
    return 0 if gate.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review a module submitted for the index.")
    parser.add_argument("repo", nargs="?", help="the author's repository: a URL or a path")
    parser.add_argument("--tag")
    parser.add_argument("--asset", help="the release bundle: a path or an https URL")
    parser.add_argument("--author-key", type=Path, help="the author's public key (PEM)")
    parser.add_argument("--author-sig", help="the author's signature, base64")
    parser.add_argument("--index", type=Path, help="the index: what was approved last")
    parser.add_argument("--out", type=Path, help="where to write the report")
    parser.add_argument("--run", action="store_true",
                        help="after stage 1 passes and you have read it: stage 2, "
                             "its own code in the sandbox")
    parser.add_argument("--selftest-sandbox", action="store_true",
                        help="prove the sandbox refuses what it must, and stop")
    args = parser.parse_args(argv)
    if args.selftest_sandbox:
        return _selftest_sandbox()
    if args.repo is None or args.tag is None:
        parser.error("a repository and --tag are needed")
    workdir = Path(tempfile.mkdtemp(prefix="trcc-review-"))
    result = review(args.repo, args.tag, workdir,
                    read_asset(args.asset) if args.asset else None,
                    raw_key(args.author_key) if args.author_key else None,
                    args.author_sig, args.index)
    diff_file = workdir / ("diff.patch" if result.since else "files.txt")
    diff_file.write_text(result.diff, encoding="utf-8")
    out = args.out or workdir / "review.md"
    out.write_text(render(result, diff_file), encoding="utf-8")
    if args.run and result.passed:
        print(f"Stage 1 passed.  Read {out} and {diff_file} before its code runs.")
        if input("Run stage 2 -- its tests and smoke, in the sandbox? [y/N] ") == "y":
            stage_two(result, workdir)
            out.write_text(render(result, diff_file), encoding="utf-8")
    elif args.run:
        print("Stage 1 failed: stage 2 does not run.")
    for gate in result.gates:
        print(f"{'PASS' if gate.passed else 'FAIL'}  {gate.name}")
    print(f"Report: {out}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())

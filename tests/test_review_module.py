"""dev/tools/review_module.py + module_bundle.py -- reviewing a submitted module.

The "author" is a throwaway git repository made from the ``hello`` skeleton
(``dev/module_template/hello``), so every gate is proven against a module of
the shape a real author starts from.  Nothing is fetched from the network.
"""
from __future__ import annotations

import io
import json
import shutil
import site
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import module_bundle
import review_module as review
import sign_module_index as signer

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATE = _REPO / "dev" / "module_template" / "hello"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=a", "-c",
                           "user.email=a@a", *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _release(repo: Path, tag: str) -> str:
    """Commit everything in *repo* and tag it: the tag's commit."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", tag, "--allow-empty")
    _git(repo, "tag", tag)
    return _git(repo, "rev-parse", "HEAD")


def _set_author(repo: Path, name: str) -> None:
    """The skeleton names no author; its author signs it with theirs."""
    manifest = repo / "module.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["author"] = name
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def author(tmp_path: Path) -> Path:
    """An author's repository: the skeleton at its root, plus the tests and
    README a repo has and a bundle leaves out."""
    repo = tmp_path / "author"
    shutil.copytree(_TEMPLATE, repo, ignore=shutil.ignore_patterns("__pycache__"))
    _set_author(repo, "Ada Author")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_hello.py").write_text("def test_x():\n    pass\n")
    (repo / "README.md").write_text("# Hello\n")
    _git(repo, "init", "-q")
    _release(repo, "v1")
    return repo


@pytest.fixture
def author_key(tmp_path: Path) -> tuple[Path, Path]:
    return signer.keygen(tmp_path / "author-key")


COMMIT = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def lint_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """ruff and pyright as the developer has them, under the test's throwaway
    HOME: a user-installed pyright imports from the real user site
    (``PYTHONUSERBASE``, as ``dev/smoke_modules.py`` passes it) and keeps its
    node in the real cache, which it only reads once it holds this version.
    Skipped where either will not start."""
    monkeypatch.setenv("PYTHONUSERBASE", site.getuserbase())
    monkeypatch.setenv("PYRIGHT_PYTHON_CACHE_DIR",
                       str(Path(site.getuserbase()).parent / ".cache" / "pyright-python"))
    for tool in ("ruff", "pyright"):
        if shutil.which(tool) is None or subprocess.run(
                [tool, "--version"], capture_output=True, check=False).returncode:
            pytest.skip(f"{tool} is what a lint gate runs, and it will not start here")


def _signed_asset(repo: Path, key_file: Path, version: str = "1") -> tuple[bytes, str]:
    asset = module_bundle.pack(repo)
    return asset, signer.author_sign(asset, "hello", version, key_file)


# ── the bundle ────────────────────────────────────────────────────────────


def test_a_bundle_is_the_same_bytes_wherever_it_is_packed(author: Path,
                                                         tmp_path: Path) -> None:
    """Another folder, another moment: the same bytes -- what lets the review
    say "built from the commit"."""
    elsewhere = tmp_path / "elsewhere" / "deep"
    shutil.copytree(author, elsewhere)
    assert module_bundle.pack(author) == module_bundle.pack(elsewhere)


def test_a_bundle_holds_only_the_module_stored_under_its_key(author: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(module_bundle.pack(author))) as z:
        infos = z.infolist()
    names = [i.filename for i in infos]
    assert names == sorted(names)
    assert all(n.startswith("hello/") for n in names)
    assert "hello/module.json" in names and "hello/ui/gui/__init__.py" in names
    assert not any("tests" in n or "README" in n or ".git" in n for n in names)
    assert {(i.compress_type, i.date_time, i.external_attr >> 16) for i in infos} == {
        (zipfile.ZIP_STORED, (1980, 1, 1, 0, 0, 0), 0o644)}
    assert module_bundle.left_out(author) == [".git", "README.md", "tests"]


@pytest.mark.parametrize("where", ["icon.svg", "core/link.py"])
def test_a_symbolic_link_is_refused_never_followed(author: Path, where: str) -> None:
    link = author / where
    link.unlink(missing_ok=True)
    link.symlink_to(Path.home() / ".ssh" / "id_ed25519")
    with pytest.raises(module_bundle.BundleError) as e:
        module_bundle.pack(author)
    assert str(e.value) == f"{where}: a symbolic link"


# ── provenance ────────────────────────────────────────────────────────────


def test_the_release_asset_must_be_what_the_commit_packs_to(
        author: Path, author_key: tuple[Path, Path]) -> None:
    asset, sig = _signed_asset(author, author_key[0])
    key = signer.public_key(author_key[1])
    assert review.provenance("hello", "1", COMMIT, asset, asset, key, sig, None).passed
    tampered = asset[:-1] + bytes([asset[-1] ^ 1])
    gate = review.provenance("hello", "1", COMMIT, tampered, asset, key, sig, None)
    assert [f.where for f in gate.findings] == ["release"]
    assert "was not built from the reviewed source" in gate.findings[0].text


@pytest.mark.parametrize(("case", "message"), [
    ("no-asset", "no release asset given (--asset)"),
    ("no-signature", "no author signature given (--author-key, --author-sig)"),
    ("other-signer", "hello 1: not signed by its author"),
    ("other-version", "hello 1: not signed by its author"),
    ("pinned", "hello 1: signed by a different author key than the installed copy"),
])
def test_provenance_refuses_what_the_author_did_not_sign(
        author: Path, author_key: tuple[Path, Path], tmp_path: Path, case: str,
        message: str) -> None:
    """The app's own check (ModuleTrust.check_release), not a copy of it."""
    asset, sig = _signed_asset(author, author_key[0])
    key, pinned = signer.public_key(author_key[1]), None
    if case == "other-signer":
        thief, _ = signer.keygen(tmp_path / "thief")
        sig = signer.author_sign(asset, "hello", "1", thief)
    elif case == "other-version":
        sig = signer.author_sign(asset, "hello", "2", author_key[0])
    elif case == "pinned":
        pinned = signer.public_key(signer.keygen(tmp_path / "old")[1])
    gate = review.provenance(
        "hello", "1", COMMIT, None if case == "no-asset" else asset, asset, key,
        None if case == "no-signature" else sig, pinned)
    assert [f.text for f in gate.findings] == [message]


# ── continuity ────────────────────────────────────────────────────────────


def test_an_update_shows_only_what_changed_since_the_approved_commit(
        author: Path) -> None:
    approved = _git(author, "rev-parse", "v1")
    (author / "core" / "__init__.py").write_text('"""New."""\nVALUE = 2\n')
    (author / "README.md").write_text("# changed, but not packed\n")
    commit = _release(author, "v2")
    gate, diff = review.continuity(author, approved, commit, "2", "1")
    assert gate.passed
    assert "+VALUE = 2" in diff
    assert "README" not in diff


def test_a_first_submission_is_read_whole(author: Path) -> None:
    gate, listing = review.continuity(author, "", _git(author, "rev-parse", "v1"))
    assert gate.notes == ["first submission -- read ALL of it"]
    assert "module.json" in listing.splitlines() and "README.md" not in listing


def test_rewritten_history_is_refused(author: Path) -> None:
    """The approved commit is gone from the branch: a diff from it would show
    nothing of what really changed."""
    approved = _git(author, "rev-parse", "v1")
    _git(author, "checkout", "-q", "--orphan", "rewritten")
    commit = _release(author, "v2")
    gate, _ = review.continuity(author, approved, commit, "2", "1")
    assert [f.where for f in gate.findings] == ["history"]


def test_an_update_must_bump_its_version(author: Path) -> None:
    commit = _git(author, "rev-parse", "v1")
    gate, _ = review.continuity(author, commit, commit, "1", "1")
    assert [f.text for f in gate.findings] == [
        "version 1 is the approved release's -- an update needs a new one, or no "
        "machine can tell them apart"]


# ── layout and code ───────────────────────────────────────────────────────


def _staged(repo: Path, root: Path, key: str = "hello") -> Path:
    staged = root / "trcc" / "modules" / key
    for rel in module_bundle.members(repo):
        (staged / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / rel, staged / rel)
    return staged


def test_the_skeleton_passes_every_static_gate(author: Path, tmp_path: Path) -> None:
    staged = _staged(author, tmp_path / "stage")
    for gate in (review.layout(author, staged), review.code(staged),
                 review.boundaries(tmp_path / "stage"), review.logging_lines(staged),
                 review.words(staged.parent, "hello")):
        assert (gate.name, gate.findings) == (gate.name, [])


@pytest.mark.parametrize(("name", "body", "message"), [
    ("assets/lib.so", b"\x7fELF", "a compiled or packed file -- only source can be "
                                  "reviewed"),
    ("assets/notes.txt", b"text\0hidden", "binary data in a file that should be text"),
])
def test_compiled_and_binary_files_are_refused(author: Path, tmp_path: Path,
                                               name: str, body: bytes,
                                               message: str) -> None:
    (author / name).parent.mkdir(parents=True, exist_ok=True)
    (author / name).write_bytes(body)
    gate = review.layout(author, _staged(author, tmp_path / "stage"))
    assert [(f.where, f.text) for f in gate.findings] == [(name, message)]


def test_a_built_in_modules_key_is_refused(author: Path, tmp_path: Path) -> None:
    gate = review.layout(author, _staged(author, tmp_path / "stage", key="rgb"))
    assert [f.text for f in gate.findings if f.hard][0] == "key 'rgb' is a built-in module's"


_NETWORK = "network -- a module fetches through host.network, from the sites it names"
_BY_TEXT = "-- runs text as code, or reaches names by text"


@pytest.mark.parametrize(("source", "hard", "text"), [
    ("import socket\n", True, f"imports socket: {_NETWORK}"),
    ("from urllib.request import urlopen\n", True, f"imports urllib.request: {_NETWORK}"),
    ("import psutil\n", True, "imports psutil: not a library a module may use"),
    ("import requests\n", True, f"imports requests: {_NETWORK}"),
    ("import leftpad\n", True, "imports leftpad: not a library a module may use"),
    ("exec('1')\n", True, f"exec {_BY_TEXT}"),
    ("eval('1')\n", True, f"eval {_BY_TEXT}"),
    ("import importlib\nimportlib.import_module(name)\n", True,
     "import_module() of a computed name"),
    ("import subprocess\n", True, "imports subprocess: runs programs"),
    ("import ctypes\n", True, "imports ctypes: calls native code"),
    ("import pickle\n", False, "imports pickle: loads data that can carry code"),
    ("import os\nos.system('x')\n", True, "os.system() -- runs a program"),
    # Disguises -- each measured MISSED before 2026-10-23, now each a hard stop.
    ("run = exec\nrun('x')\n", True, f"exec {_BY_TEXT}"),
    ("import builtins\n", True, "imports builtins: reaches Python's own functions "
                                 "by name"),
    ("__builtins__['exec']('x')\n", True, f"__builtins__ {_BY_TEXT}"),
    ("globals()['__builtins__']\n", True, f"globals {_BY_TEXT}"),
    ("import sys\nsys.modules['os'].system('id')\n", True,
     "sys.modules -- reaches any module by name"),
    ("import os\ngetattr(os, 'sys' + 'tem')('id')\n", True,
     "getattr() of a computed name"),
    ("().__class__.__base__.__subclasses__()\n", True,
     ".__subclasses__ -- reaches Python's internals"),
    ("f.__globals__\n", True, ".__globals__ -- reaches Python's internals"),
    ("import asyncio\nasyncio.open_connection('evil.example', 80)\n", True,
     ".open_connection() -- network; a module fetches through host.network, "
     "from the sites it names"),
    ("import os\nos.execv('/bin/sh', ['sh'])\n", True, "os.execv() -- runs a program"),
    ("__import__('so' + 'cket')\n", True, f"__import__ {_BY_TEXT}"),
    ("open(p, 'w')\n", False, "open() for writing"),
    ("open(p, mode=m)\n", False, "open() for writing"),
    ("p.write_bytes(b'')\n", False, ".write_bytes() -- changes files"),
    (f"X = '{'QUJD' * 60}'\n", False, "a 240-character literal with no spaces -- "
                                      "encoded data?"),
])
def test_each_code_flag_is_seen(tmp_path: Path, source: str, hard: bool,
                                text: str) -> None:
    staged = tmp_path / "hello"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "x.py").write_text(source)
    findings = review.code(staged).findings
    assert [(f.hard, f.text) for f in findings][-1] == (hard, text)


@pytest.mark.parametrize("source", [
    "import json\nimport numpy\nfrom PySide6.QtGui import QImage\nimport typer\n",
    "open(p)\nopen(p, 'rb')\nopen(p, encoding='utf-8')\n",
    "from ..core import x\nfrom trcc.core.models import Wire\n",
    "import re\nre.compile('x')\n",                  # not Python's compile
    "dialog.exec()\n",                                # a Qt dialog, not exec
    "getattr(obj, 'name', None)\nhasattr(obj, 'x')\n",
    "import asyncio\nasyncio.sleep(1)\n",
    "import sys\nsys.argv\n",
    f"'''{'a long docstring with spaces ' * 20}'''\n",
])
def test_ordinary_code_raises_no_flag(tmp_path: Path, source: str) -> None:
    staged = tmp_path / "hello"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "x.py").write_text(source)
    assert review.code(staged).findings == []


def test_a_silent_function_and_a_missing_word_fail(author: Path, tmp_path: Path) -> None:
    (author / "core" / "quiet.py").write_text("def quiet():\n    return 1\n")
    table = json.loads((author / "i18n.json").read_text(encoding="utf-8"))
    del table["de"]
    (author / "i18n.json").write_text(json.dumps(table), encoding="utf-8")
    staged = _staged(author, tmp_path / "stage")
    assert [f.where for f in review.logging_lines(staged).findings] == [
        "core/quiet.py::quiet"]
    assert [f.where for f in review.words(staged.parent, "hello").findings] == [
        "i18n.json de"]


def test_a_module_reaching_past_its_host_fails(author: Path, tmp_path: Path) -> None:
    (author / "core" / "reach.py").write_text("from trcc.services.display import D\n")
    _staged(author, tmp_path / "stage")
    gate = review.boundaries(tmp_path / "stage")
    assert [(f.where, f.text) for f in gate.findings] == [(
        "trcc/modules/hello/core/reach.py:1",
        "trcc.services.display (a module reaches past its host)")]


def test_a_copied_body_is_pointed_out(author: Path, tmp_path: Path) -> None:
    body = "".join(f"    x{i} = {i}\n" for i in range(7))
    (author / "core" / "a.py").write_text(f"def one():\n{body}\n\ndef two():\n{body}")
    gate = review.judgement(_staged(author, tmp_path / "stage"), "hello")
    copies = [f for f in gate.findings if "the same body" in f.text]
    assert len(copies) == 1 and not copies[0].hard


# ── end to end ────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("lint_tools")
def test_a_clean_release_passes_stage_one_and_leaves_no_worktree(
        author: Path, author_key: tuple[Path, Path], tmp_path: Path) -> None:
    asset, sig = _signed_asset(author, author_key[0])
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = review.review(str(author), "v1", workdir, asset,
                           signer.public_key(author_key[1]), sig)
    assert {g.name: [f.text for f in g.findings if f.hard] for g in result.gates} == {
        g.name: [] for g in result.gates}
    assert [g.name for g in result.gates][-3:] == [
        "ruff", "pyright", "For your judgement (KISS / SOLID / DRY)"]
    assert str(workdir / "tree") not in _git(_REPO, "worktree", "list")
    report = review.render(result, workdir / "files.txt")
    assert "## Verdict: stage 1 passed" in report
    assert "NOT listable yet: read this report and the diff, then run again with --run" in report


@pytest.mark.usefixtures("lint_tools")
def test_the_lint_gates_fail_on_what_they_should(author: Path, tmp_path: Path) -> None:
    """Proof the two gates can fail: an unused import for ruff, a wrong type
    for pyright -- in the module, read with this tree's own settings."""
    (author / "core" / "bad.py").write_text(
        '"""Bad."""\nimport json\n\nVALUE: int = "text"\n')
    _release(author, "v2")
    workdir = tmp_path / "work"
    workdir.mkdir()
    gates = {g.name: g for g in review.review(str(author), "v2", workdir).gates}
    assert [f.where for f in gates["ruff"].findings] == [
        "src/trcc/modules/hello/core/bad.py:2:8"]
    assert [f.where for f in gates["pyright"].findings] == ["core/bad.py:4"]


# ── stage 2: the sandbox, its tests, the smoke ────────────────────────────
# A real container needs the image this machine built; ``review_module.py
# --selftest-sandbox`` runs that half.  Here podman is replaced by canned runs,
# so what is proven is what the tool asks for and what it makes of the answer.


def test_the_sandbox_command_is_exactly_this(tmp_path: Path) -> None:
    """Every flag that keeps the module in: drop one and this fails."""
    assert review.sandbox("img", tmp_path, "python", "-c", "x") == [
        "podman", "run", "--rm", "--network=none", "--cap-drop=all",
        "--security-opt=no-new-privileges", "--read-only",
        "--tmpfs=/tmp:rw,size=1g,mode=1777",
        "--tmpfs=/home/ubuntu:rw,size=256m,mode=1777",
        "--memory=4g", "--pids-limit=1024", "--timeout=900",
        "--env=HOME=/home/ubuntu", "--env=PYTHONDONTWRITEBYTECODE=1",
        "--env=PYTHONPATH=/review/src", "--env=QT_QPA_PLATFORM=offscreen",
        f"--volume={tmp_path}:/review:ro,Z", "--workdir=/review", "img",
        "python", "-c", "x"]


def test_the_image_changes_with_its_dependencies(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    before = review.image_tag()
    changed = tmp_path / "Containerfile"
    changed.write_bytes(review.CONTAINERFILE.read_bytes() + b"\n# changed\n")
    monkeypatch.setattr(review, "CONTAINERFILE", changed)
    assert review.image_tag() != before
    assert before.startswith("localhost/trcc-review:")


def _canned(monkeypatch: pytest.MonkeyPatch, code: int, out: str) -> list[tuple[str, ...]]:
    """podman replaced: every run returns *code* and *out*; the commands asked
    for are recorded."""
    asked: list[tuple[str, ...]] = []

    def run(workdir: Path, image: str, tree: Path, *command: str):
        asked.append(command)
        return subprocess.CompletedProcess(command, code, out, "")

    monkeypatch.setattr(review, "_contained", run)
    return asked


def test_the_sandbox_must_hold_every_probe(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    held = {"no network": True, "tree read-only": True, "not root": True}
    _canned(monkeypatch, 0, json.dumps(held))
    assert review.proven(tmp_path, "img", tmp_path).passed
    _canned(monkeypatch, 0, json.dumps({**held, "no network": False}))
    gate = review.proven(tmp_path, "img", tmp_path)
    assert [(f.where, f.text) for f in gate.findings] == [
        ("no network", "NOT held inside the sandbox")]
    _canned(monkeypatch, 125, "")
    assert [f.where for f in review.proven(tmp_path, "img", tmp_path).findings] == ["probe"]


def test_the_probe_runs_isolated_and_names_this_machines_home(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _canned(monkeypatch, 0, "{}")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1234")
    review.proven(tmp_path, "img", tmp_path)
    assert asked == [(review.PYTHON, "-I", "-c", review.PROBE, str(Path.home()),
                      "/run/user/1234")]


def test_the_export_holds_the_module_its_tests_and_no_git(
        author: Path, tmp_path: Path) -> None:
    staged = _staged(author, tmp_path / "stage")
    tree = review.export(tmp_path, staged, "hello", author / "tests")
    assert (tree / "src" / "trcc" / "modules" / "hello" / "module.json").is_file()
    assert sorted(p.name for p in (tree / "tests" / "module_hello").iterdir()) == [
        "__init__.py", "test_hello.py"]
    assert (tree / "tests" / "conftest.py").is_file()
    assert not (tree / ".git").exists()
    assert (tree / "dev" / ".trcc" / "data").is_dir() and (tree / "dev" / ".trcc-user").is_dir()


def test_a_module_without_tests_fails_without_running_anything(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _canned(monkeypatch, 0, "")
    gate = review.own_tests(tmp_path, "img", tmp_path, "hello")
    assert [f.text for f in gate.findings] == ["no tests -- a module ships its own"]
    assert asked == []


@pytest.mark.parametrize(("code", "failures"), [
    (0, []), (1, ["failed, exit 1"]), (5, ["no test was collected"])])
def test_its_tests_pass_or_say_why_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       code: int, failures: list[str]) -> None:
    (tmp_path / "tests" / "module_hello").mkdir(parents=True)
    asked = _canned(monkeypatch, code, "== 1 passed ==\n")
    gate = review.own_tests(tmp_path, "img", tmp_path, "hello")
    assert [f.text for f in gate.findings] == failures
    assert asked == [(review.PYTHON, "-m", "pytest", "tests/module_hello", "-q",
                      "-n", "0", "-p", "no:cacheprovider")]


@pytest.mark.parametrize(("code", "out", "failures"), [
    (0, "    [OK]     cli    ['hello']\n  Summary: 13 pass\n", []),
    (1, "    [FAIL]   gui    tab never shown\n  Summary: 12 pass, 1 fail\n",
     ["gui    tab never shown"]),
    (1, "", ["exit 1: "]),
])
def test_the_smoke_drives_the_module_alone(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch, code: int,
                                           out: str, failures: list[str]) -> None:
    asked = _canned(monkeypatch, code, out)
    gate = review.smoke(tmp_path, "img", tmp_path, "hello")
    assert [f.text for f in gate.findings] == failures
    assert asked == [(review.PYTHON, "dev/smoke_modules.py", "--only", "hello")]


def test_nothing_of_the_authors_runs_when_the_sandbox_leaks(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    leaky = review.Gate("Sandbox")
    leaky.fail("no network", "NOT held inside the sandbox")
    monkeypatch.setattr(review, "ensure_image", lambda workdir: "img")
    monkeypatch.setattr(review, "export", lambda *a: tmp_path)
    monkeypatch.setattr(review, "proven", lambda *a: leaky)
    monkeypatch.setattr(review, "own_tests", lambda *a: ran.append("tests"))
    monkeypatch.setattr(review, "smoke", lambda *a: ran.append("smoke"))
    result = review.Review("hello", "1", COMMIT, [], "", "")
    review.stage_two(result, tmp_path)
    assert ran == [] and [g.name for g in result.gates] == ["Sandbox"]
    assert result.ran and not result.passed


@pytest.mark.parametrize(("hard", "ran", "verdict"), [
    (True, False, "## Verdict: NOT listable -- see each FAIL above"),
    (False, False, "## Verdict: stage 1 passed"),
    (False, True, "## Verdict: listable -- if your read agrees"),
])
def test_the_verdict_says_listable_only_after_both_stages(
        tmp_path: Path, hard: bool, ran: bool, verdict: str) -> None:
    gate = review.Gate("Code")
    if hard:
        gate.fail("x.py:1", "exec() -- runs text as code")
    result = review.Review("hello", "1", COMMIT, [gate], "", "", ran=ran)
    report = review.render(result, tmp_path / "files.txt")
    assert verdict in report
    assert ("sign_module_index.py add" in report) == (not hard and ran)


def test_the_add_command_carries_the_sites_it_fetches_from(tmp_path: Path) -> None:
    """They go into the signed index, so an update naming a new one is asked
    about again."""
    result = review.Review("weather", "3", COMMIT, [], "", "", ("network",),
                           ("api.example.com",), ran=True)
    report = review.render(result, tmp_path / "files.txt")
    assert ("--capabilities network --network-hosts api.example.com --title '' "
            "--summary '' --author '' --bundle" in report)


@pytest.mark.parametrize(("stage_one_passes", "answer", "asked", "ran"), [
    (True, "y", True, True),
    (True, "n", True, False),
    (True, "", True, False),        # Enter is No
    (False, "y", False, False),     # a failed stage 1 never reaches the question
])
def test_its_code_runs_only_after_a_yes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage_one_passes: bool,
        answer: str, asked: bool, ran: bool) -> None:
    """``--run`` points at the report, then asks; only "y" runs stage 2."""
    gate = review.Gate("Code")
    if not stage_one_passes:
        gate.fail("x.py:1", "exec() -- runs text as code")
    monkeypatch.setattr(review, "review", lambda *a: review.Review(
        "hello", "1", COMMIT, [gate], "", ""))
    questions: list[str] = []
    stage_two: list[bool] = []
    monkeypatch.setattr("builtins.input", lambda q: questions.append(q) or answer)
    monkeypatch.setattr(review, "stage_two", lambda result, workdir: stage_two.append(True))
    review.main(["repo", "--tag", "v1", "--run", "--out", str(tmp_path / "r.md")])
    assert (questions != [], stage_two != []) == (asked, ran)

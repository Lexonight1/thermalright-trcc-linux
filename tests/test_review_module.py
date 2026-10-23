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
import i18n_gaps
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


@pytest.fixture
def author(tmp_path: Path) -> Path:
    """An author's repository: the skeleton at its root, every word in every
    language, plus the tests and README a repo has and a bundle leaves out."""
    repo = tmp_path / "author"
    shutil.copytree(_TEMPLATE, repo, ignore=shutil.ignore_patterns("__pycache__"))
    words = json.loads((repo / "i18n.json").read_text(encoding="utf-8"))["en"]
    (repo / "i18n.json").write_text(json.dumps(
        {lang: dict(words) for lang in i18n_gaps.languages()}), encoding="utf-8")
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


@pytest.mark.parametrize(("source", "hard", "text"), [
    ("import socket\n", True, "imports socket: network -- TRCC has no network "
                              "capability yet"),
    ("from urllib.request import urlopen\n", True, "imports urllib.request: network "
                                                   "-- TRCC has no network capability yet"),
    ("import psutil\n", True, "imports psutil: not a library a module may use"),
    ("import requests\n", True, "imports requests: network -- TRCC has no network "
                                "capability yet"),
    ("import leftpad\n", True, "imports leftpad: not a library a module may use"),
    ("exec('1')\n", True, "exec() -- runs text as code"),
    ("eval('1')\n", True, "eval() -- runs text as code"),
    ("import importlib\nimportlib.import_module(name)\n", True,
     "import_module() of a computed name"),
    ("import subprocess\n", False, "imports subprocess: runs programs"),
    ("import ctypes\n", False, "imports ctypes: calls native code"),
    ("import pickle\n", False, "imports pickle: loads data that can carry code"),
    ("import os\nos.system('x')\n", False, "os.system()"),
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
    assert "NOT listable yet: stage 2" in report


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

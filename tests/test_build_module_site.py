"""The module website -- ``dev/tools/build_module_site.py``.

Built from a real signed index: throwaway keys, signed by
``dev/tools/sign_module_index.py``, opened by the app's own ``ModuleTrust``.
The page may say only what the verified index says, and a file TRCC would
refuse is never published.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import build_module_site as site
import module_bundle
import new_module
import sign_module_index as tool

from trcc.adapters.modules import Ed25519Checker
from trcc.core.errors import UntrustedModule
from trcc.services.module_trust import ModuleTrust

AUTHOR = 'Ada <b>&</b> "Author"'
SUMMARY = "Says hello <script>alert(1)</script>"


class Index:
    """The maintainer's side: keys, a root, and an index on disk."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.keys, self.source = folder / "keys", folder / "modules"
        self.keys.mkdir(parents=True)
        self.source.mkdir()
        for name in ("root", "index", "author"):
            tool.keygen(self.keys / name)
        self.trust = ModuleTrust(Ed25519Checker(), [self.raw("root")])
        self.now = datetime.now(timezone.utc)
        root = tool.make_root(1, [self.raw("index")], 365, self.now,
                              self.keys / "root.key", self.trust)
        (self.source / "root.json").write_bytes(root)
        self.root = self.trust.open_root(root)
        self.signed = tool._signed(Path("/nonexistent"))
        self.write()

    def raw(self, name: str) -> str:
        return tool.public_key(self.keys / f"{name}.pub")

    def write(self, days: int = 90, now: datetime | None = None) -> None:
        data = tool.reissue(self.signed, self.root, days, now or self.now,
                            self.keys / "index.key", self.trust)
        (self.source / "index.json").write_bytes(data)
        self.signed = json.loads(data)["signed"]

    def list_greeter(self) -> bytes:
        folder = new_module.create("greeter", "Greeter", SUMMARY, author=AUTHOR,
                                   into=self.folder / "greeter")
        bundle = module_bundle.pack(folder)
        module, said = tool.entry_from_bundle(bundle)
        self.signed = tool.add(self.signed, module, bundle, {
            **said, "url": "https://example.test/greeter-1.zip", "commit": "ab" * 20,
            "min_trcc": "9.12.0", "author_key": self.raw("author"),
            "author_sig": tool.author_sign(bundle, module, said["version"],
                                           self.keys / "author.key")}, self.trust)
        self.write()
        return bundle

    def build(self, out: Path) -> str:
        site.build(self.source, out, [self.raw("root")], self.now)
        return (out / "modules" / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def index(tmp_path: Path) -> Index:
    return Index(tmp_path / "maintainer")


def test_an_empty_index_publishes_both_files_as_signed(index: Index, tmp_path: Path) -> None:
    """The first publication: nothing listed yet, so "Get modules" shows an
    empty list instead of failing.  The files are served byte for byte."""
    page = index.build(tmp_path / "out")
    for name in ("root.json", "index.json"):
        assert (tmp_path / "out/modules" / name).read_bytes() == (
            index.source / name).read_bytes()
    assert "No modules are listed yet." in page
    assert (tmp_path / "out/index.html").read_text(encoding="utf-8").count(
        'href="modules/"') == 1


def test_a_listed_module_is_shown_as_signed_and_every_text_is_escaped(
        index: Index, tmp_path: Path) -> None:
    """MUTATION CHECK: drop ``html.escape`` in ``_e``."""
    index.list_greeter()
    page = index.build(tmp_path / "out")
    shown = ("<h2>Greeter", "Says hello &lt;script&gt;alert(1)&lt;/script&gt;",
             "by Ada &lt;b&gt;&amp;&lt;/b&gt; &quot;Author&quot;", "asks for: events")
    assert [text for text in shown if text not in page] == []
    assert "<script>" not in page and "<b>&</b>" not in page


def test_a_release_taken_back_is_marked_on_its_tile_not_listed_twice(
        index: Index, tmp_path: Path) -> None:
    """Judged as the app judges it (``ModuleTrust.revoked``)."""
    index.list_greeter()
    index.signed = tool.revoke(index.signed, "greeter", "sends your readings away", "")
    index.write()
    page = index.build(tmp_path / "out")
    assert ('<div class="module revoked" id="greeter">' in page,
            "<b>Taken back:</b> sends your readings away", "Taken back</h2>" in page) == (
        True, "<b>Taken back:</b> sends your readings away", False)
    assert page.count("<b>Taken back:</b> sends your readings away") == 1


def test_a_revocation_of_a_module_no_longer_listed_still_says_why(
        index: Index, tmp_path: Path) -> None:
    index.signed = tool.revoke(index.signed, "gone", "it mined coins", "")
    index.write()
    page = index.build(tmp_path / "out")
    assert '<div class="module revoked"><b>gone</b>: it mined coins</div>' in page


def test_an_expired_index_is_published_and_says_so(index: Index, tmp_path: Path) -> None:
    """Expired is still authentic -- its revocations count -- so it is
    published, and the page says nothing installs from it."""
    index.write(days=1, now=index.now - timedelta(days=3))
    page = index.build(tmp_path / "out")
    assert "This list has expired" in page


@pytest.mark.parametrize("damage", ["reason", "root"])
def test_a_file_trcc_would_refuse_is_never_published(
        index: Index, tmp_path: Path, damage: str) -> None:
    """A changed byte under the signature, or a root no pinned key signed.
    MUTATION CHECK: write the files before opening them."""
    if damage == "reason":
        index.signed = tool.revoke(index.signed, "gone", "it mined coins", "")
        index.write()
        doc = json.loads((index.source / "index.json").read_text(encoding="utf-8"))
        doc["signed"]["revoked"][0]["reason"] = "nothing to see"
        (index.source / "index.json").write_text(json.dumps(doc), encoding="utf-8")
    keys = [index.raw("root")] if damage == "reason" else [index.raw("author")]
    with pytest.raises(UntrustedModule):
        site.build(index.source, tmp_path / "out", keys, index.now)
    assert not (tmp_path / "out").exists()


def test_the_command_line_refuses_with_one_line_and_exit_1(
        index: Index, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Against TRCC's pinned release keys, a throwaway root is refused."""
    assert site.main([str(index.source), "-o", str(tmp_path / "out")]) == 1
    assert capsys.readouterr().err == (
        "NOT BUILT -- root.json: not signed by a key TRCC trusts\n")
    assert not (tmp_path / "out").exists()


def test_the_command_line_builds_with_the_root_key_it_is_given(
        index: Index, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert site.main([str(index.source), "-o", str(out),
                      "--trust", str(index.keys / "root.pub")]) == 0
    assert capsys.readouterr().out.startswith(f"built {out}/modules: index 1, 0 module(s)")


# ── an author's own module, made outside TRCC's tree ──────────────────────


def test_new_module_out_makes_the_folder_the_module_with_its_authors_name(
        tmp_path: Path) -> None:
    """The review reads the author's repository root as the module folder."""
    folder = new_module.create("weather", "Weather", "The forecast.",
                               author="Ada Author", into=tmp_path / "repo")
    manifest = json.loads((folder / "module.json").read_text(encoding="utf-8"))
    assert (folder, manifest["key"], manifest["author"], manifest["guide"],
            (folder / "app.py").exists()) == (
        tmp_path / "repo", "weather", "Ada Author", "", True)


def test_new_module_out_needs_an_author_who_is_not_trcc(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for author in ([], ["--author", "Lexonight"]):
        with pytest.raises(SystemExit):
            new_module.main(["weather", "--title", "W", "--summary", "s",
                             "--out", str(tmp_path / "repo"), *author])
        assert "--out needs --author" in capsys.readouterr().err
    assert not (tmp_path / "repo").exists()

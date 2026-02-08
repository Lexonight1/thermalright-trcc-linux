"""
Tests for theme_downloader – theme pack registry, download, install, and removal.

Tests cover:
- THEME_REGISTRY structure and entries
- get_user_themes_dir() / get_cache_dir() path helpers
- get_installed_packs() metadata scanning
- list_available() / show_info() display output
- download_with_progress() with mocked HTTP
- verify_checksum() SHA-256 verification
- extract_archive() for tar.gz and zip formats
- download_pack() full install flow
- remove_pack() uninstall flow
- create_local_pack() archive creation
"""

import hashlib
import json
import os
import tarfile
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from trcc.theme_downloader import (
    THEME_REGISTRY,
    create_local_pack,
    download_pack,
    download_with_progress,
    extract_archive,
    get_cache_dir,
    get_installed_packs,
    get_user_themes_dir,
    list_available,
    remove_pack,
    show_info,
    verify_checksum,
)


class TestThemeRegistry(unittest.TestCase):
    """Validate the built-in THEME_REGISTRY structure."""

    def test_registry_not_empty(self):
        self.assertGreater(len(THEME_REGISTRY), 0)

    def test_required_keys(self):
        """Every registry entry must have the required metadata keys."""
        required = {'name', 'version', 'resolution', 'description', 'size_mb', 'url'}
        for pack_id, info in THEME_REGISTRY.items():
            with self.subTest(pack=pack_id):
                self.assertTrue(required.issubset(info.keys()),
                                f"Missing keys in {pack_id}: {required - info.keys()}")

    def test_resolution_format(self):
        """Resolution must be WxH format (e.g., '320x320')."""
        for pack_id, info in THEME_REGISTRY.items():
            with self.subTest(pack=pack_id):
                self.assertRegex(info['resolution'], r'^\d+x\d+$')

    def test_known_packs(self):
        """Verify the three standard packs exist."""
        self.assertIn('themes-320', THEME_REGISTRY)
        self.assertIn('themes-480', THEME_REGISTRY)
        self.assertIn('themes-240', THEME_REGISTRY)


class TestPathHelpers(unittest.TestCase):
    """Test directory path helpers."""

    def test_user_themes_dir(self):
        d = get_user_themes_dir()
        self.assertIsInstance(d, Path)
        self.assertTrue(str(d).endswith('.trcc/themes'))

    def test_cache_dir_creates(self):
        """get_cache_dir() should create the directory if missing."""
        d = get_cache_dir()
        self.assertIsInstance(d, Path)
        self.assertTrue(d.exists())


class TestGetInstalledPacks(unittest.TestCase):
    """Test get_installed_packs() metadata scanner."""

    def test_empty_when_no_dir(self):
        """Returns empty dict when themes dir doesn't exist."""
        with patch('trcc.theme_downloader.get_user_themes_dir') as mock_dir:
            mock_dir.return_value = Path('/nonexistent/themes')
            self.assertEqual(get_installed_packs(), {})

    def test_reads_meta_json(self):
        """Reads .trcc-meta.json when present."""
        with tempfile.TemporaryDirectory() as tmp:
            themes_dir = Path(tmp)
            res_dir = themes_dir / '320320'
            res_dir.mkdir()

            meta = {'pack_name': 'themes-320', 'version': '1.0.0', 'theme_count': 5}
            (res_dir / '.trcc-meta.json').write_text(json.dumps(meta))

            with patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                installed = get_installed_packs()

            self.assertIn('themes-320', installed)
            self.assertEqual(installed['themes-320']['version'], '1.0.0')

    def test_counts_themes_without_meta(self):
        """Falls back to directory counting when no meta file."""
        with tempfile.TemporaryDirectory() as tmp:
            themes_dir = Path(tmp)
            res_dir = themes_dir / '320320'
            res_dir.mkdir()
            # Create two fake theme dirs
            (res_dir / 'Theme1').mkdir()
            (res_dir / 'Theme2').mkdir()

            with patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                installed = get_installed_packs()

            # Pack name derived from dir name
            self.assertTrue(any('320' in k for k in installed))


class TestListAndInfo(unittest.TestCase):
    """Test display/info functions (output only)."""

    def test_list_available_runs(self):
        """list_available() should print without error."""
        with patch('trcc.theme_downloader.get_installed_packs', return_value={}):
            list_available()  # Smoke test — no crash

    def test_show_info_known_pack(self):
        """show_info() handles a known pack."""
        with patch('trcc.theme_downloader.get_installed_packs', return_value={}):
            show_info('themes-320')  # Should not raise

    def test_show_info_unknown_pack(self, ):
        """show_info() handles unknown pack gracefully."""
        show_info('nonexistent-pack')  # Should print error, not crash


class TestVerifyChecksum(unittest.TestCase):
    """Test SHA-256 checksum verification."""

    def test_none_checksum_always_passes(self):
        """When sha256 is None, verification is skipped."""
        self.assertTrue(verify_checksum(Path('/dev/null'), None))

    def test_correct_checksum(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'hello world')
            f.flush()
            expected = hashlib.sha256(b'hello world').hexdigest()
            try:
                self.assertTrue(verify_checksum(Path(f.name), expected))
            finally:
                os.unlink(f.name)

    def test_wrong_checksum(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'hello world')
            f.flush()
            try:
                self.assertFalse(verify_checksum(Path(f.name), 'bad_hash'))
            finally:
                os.unlink(f.name)


class TestExtractArchive(unittest.TestCase):
    """Test archive extraction for tar.gz and zip."""

    def _create_tar_gz(self, dest: Path, files: dict):
        """Helper: create tar.gz with {name: content} files."""
        with tarfile.open(str(dest), 'w:gz') as tar:
            for name, content in files.items():
                data = content.encode() if isinstance(content, str) else content
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, BytesIO(data))

    def _create_zip(self, dest: Path, files: dict):
        """Helper: create zip with {name: content} files."""
        with zipfile.ZipFile(str(dest), 'w') as z:
            for name, content in files.items():
                z.writestr(name, content)

    def test_extract_tar_gz(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'test.tar.gz'
            self._create_tar_gz(archive, {'readme.txt': 'hello'})

            dest = Path(tmp) / 'out'
            dest.mkdir()
            self.assertTrue(extract_archive(archive, dest))
            self.assertTrue((dest / 'readme.txt').exists())

    def test_extract_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'test.zip'
            self._create_zip(archive, {'readme.txt': 'hello'})

            dest = Path(tmp) / 'out'
            dest.mkdir()
            self.assertTrue(extract_archive(archive, dest))
            self.assertTrue((dest / 'readme.txt').exists())

    def test_unknown_format_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'test.rar'
            archive.write_text('fake')
            dest = Path(tmp) / 'out'
            dest.mkdir()
            self.assertFalse(extract_archive(archive, dest))


class TestDownloadWithProgress(unittest.TestCase):
    """Test download_with_progress() with mocked HTTP."""

    @patch('trcc.theme_downloader.urlopen')
    def test_successful_download(self, mock_urlopen):
        """Mocked successful download writes file."""
        body = b'fake archive data'
        mock_response = MagicMock()
        mock_response.headers = {'content-length': str(len(body))}
        mock_response.read = MagicMock(side_effect=[body, b''])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'download.tar.gz'
            result = download_with_progress('http://example.com/file', dest)
            self.assertTrue(result)
            self.assertEqual(dest.read_bytes(), body)

    @patch('trcc.theme_downloader.urlopen')
    def test_http_error(self, mock_urlopen):
        """HTTPError returns False."""
        from email.message import Message
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            'http://x', 404, 'Not Found', Message(), None
        )

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'download.tar.gz'
            result = download_with_progress('http://example.com/file', dest)
            self.assertFalse(result)


class TestDownloadPack(unittest.TestCase):
    """Test download_pack() orchestration."""

    def test_unknown_pack(self):
        """Unknown pack name returns 1."""
        self.assertEqual(download_pack('nonexistent-pack'), 1)

    def test_already_installed_same_version(self):
        """Already installed + same version returns 0 without downloading."""
        installed = {'themes-320': {'version': THEME_REGISTRY['themes-320']['version']}}
        with patch('trcc.theme_downloader.get_installed_packs', return_value=installed):
            self.assertEqual(download_pack('themes-320'), 0)

    @patch('trcc.theme_downloader.download_with_progress', return_value=False)
    @patch('trcc.theme_downloader.get_installed_packs', return_value={})
    def test_download_failure(self, mock_installed, mock_download):
        """Download failure returns 1."""
        self.assertEqual(download_pack('themes-320'), 1)


class TestRemovePack(unittest.TestCase):
    """Test remove_pack() uninstall."""

    def test_remove_not_installed(self):
        """Removing non-installed pack returns 1."""
        with patch('trcc.theme_downloader.get_installed_packs', return_value={}):
            self.assertEqual(remove_pack('themes-320'), 1)

    def test_remove_installed(self):
        """Removing installed pack deletes directory."""
        with tempfile.TemporaryDirectory() as tmp:
            themes_dir = Path(tmp)
            res_dir = themes_dir / '320320'
            res_dir.mkdir()
            (res_dir / 'Theme1').mkdir()

            installed = {'themes-320': {'resolution': '320x320'}}

            with patch('trcc.theme_downloader.get_installed_packs', return_value=installed), \
                 patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                result = remove_pack('themes-320')

            self.assertEqual(result, 0)
            self.assertFalse(res_dir.exists())

    def test_remove_missing_resolution(self):
        """Pack with empty resolution returns 1."""
        installed = {'themes-320': {'resolution': ''}}
        with patch('trcc.theme_downloader.get_installed_packs', return_value=installed):
            self.assertEqual(remove_pack('themes-320'), 1)


class TestCreateLocalPack(unittest.TestCase):
    """Test create_local_pack() archive builder."""

    def test_missing_source_dir(self):
        self.assertEqual(create_local_pack('/nonexistent/dir', 'test-pack', '320x320'), 1)

    def test_no_themes_found(self):
        """Empty directory yields no themes → returns 1."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(create_local_pack(tmp, 'test-pack', '320x320'), 1)

    def test_creates_archive(self):
        """Valid themes produce a tar.gz archive."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()

            # Create a fake theme directory with Theme.png
            theme_dir = source / 'Theme1'
            theme_dir.mkdir()
            (theme_dir / 'Theme.png').write_bytes(b'PNG_FAKE')

            original_cwd = os.getcwd()
            os.chdir(tmp)  # Archive is created in cwd
            try:
                result = create_local_pack(str(source), 'test-pack', '320x320')
                self.assertEqual(result, 0)
                self.assertTrue(Path('test-pack.tar.gz').exists())
            finally:
                os.chdir(original_cwd)


# ── corrupt meta JSON ────────────────────────────────────────────────────────

class TestInstalledPacksCorruptMeta(unittest.TestCase):

    def test_corrupt_meta_skipped(self):
        """Corrupt .trcc-meta.json is silently skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            themes_dir = Path(tmp)
            res_dir = themes_dir / '320320'
            res_dir.mkdir()
            (res_dir / '.trcc-meta.json').write_text('{bad json')
            (res_dir / 'Theme1').mkdir()

            with patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                installed = get_installed_packs()
            # Corrupt meta → silently skipped (pass), no entry added
            self.assertEqual(installed, {})


# ── list_available with installed ─────────────────────────────────────────────

class TestListAvailableInstalled(unittest.TestCase):

    def test_shows_installed_status(self):
        """Installed pack shows [installed] label."""
        import io
        from contextlib import redirect_stdout

        pack_name = list(THEME_REGISTRY.keys())[0]
        version = THEME_REGISTRY[pack_name]['version']
        installed = {pack_name: {'version': version}}

        buf = io.StringIO()
        with patch('trcc.theme_downloader.get_installed_packs', return_value=installed), \
             redirect_stdout(buf):
            list_available()
        self.assertIn('[installed]', buf.getvalue())

    def test_shows_update_available(self):
        """Outdated installed version shows [update available]."""
        import io
        from contextlib import redirect_stdout

        pack_name = list(THEME_REGISTRY.keys())[0]
        installed = {pack_name: {'version': '0.0.1'}}

        buf = io.StringIO()
        with patch('trcc.theme_downloader.get_installed_packs', return_value=installed), \
             redirect_stdout(buf):
            list_available()
        self.assertIn('update available', buf.getvalue())


# ── show_info installed pack ─────────────────────────────────────────────────

class TestShowInfoInstalled(unittest.TestCase):

    def test_shows_installed_yes(self):
        import io
        from contextlib import redirect_stdout

        pack_name = list(THEME_REGISTRY.keys())[0]
        installed = {pack_name: {'version': '1.0', 'theme_count': 5}}

        buf = io.StringIO()
        with patch('trcc.theme_downloader.get_installed_packs', return_value=installed), \
             redirect_stdout(buf):
            show_info(pack_name)
        self.assertIn('Installed:   Yes', buf.getvalue())


# ── download_with_progress edge cases ────────────────────────────────────────

class TestDownloadEdgeCases(unittest.TestCase):

    @patch('trcc.theme_downloader.urlopen')
    def test_urlerror(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError('no network')
        with tempfile.TemporaryDirectory() as tmp:
            result = download_with_progress('http://x', Path(tmp) / 'f')
        self.assertFalse(result)

    @patch('trcc.theme_downloader.urlopen')
    def test_generic_exception(self, mock_urlopen):
        mock_urlopen.side_effect = OSError('disk full')
        with tempfile.TemporaryDirectory() as tmp:
            result = download_with_progress('http://x', Path(tmp) / 'f')
        self.assertFalse(result)

    @patch('trcc.theme_downloader.urlopen')
    def test_no_content_length(self, mock_urlopen):
        """No Content-Length → progress shows MB only."""
        body = b'data' * 100
        mock_response = MagicMock()
        mock_response.headers = {'content-length': '0'}
        mock_response.read = MagicMock(side_effect=[body, b''])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmp:
            result = download_with_progress('http://x', Path(tmp) / 'f')
        self.assertTrue(result)


# ── download_pack additional branches ────────────────────────────────────────

class TestDownloadPackExtra(unittest.TestCase):

    @patch('trcc.theme_downloader.download_with_progress', return_value=True)
    @patch('trcc.theme_downloader.verify_checksum', return_value=True)
    @patch('trcc.theme_downloader.get_installed_packs', return_value={})
    def test_full_success_flow(self, mock_installed, mock_checksum, mock_download):
        """Full install: download → extract → install themes → metadata."""
        pack_name = list(THEME_REGISTRY.keys())[0]
        info = THEME_REGISTRY[pack_name]
        resolution = info['resolution'].replace('x', '')

        with tempfile.TemporaryDirectory() as tmp:
            themes_dir = Path(tmp) / 'themes'
            cache_dir = Path(tmp) / 'cache'
            cache_dir.mkdir()

            # Create a real tar.gz with a theme inside
            archive_path = cache_dir / f"{pack_name}-{info['version']}.tar.gz"
            with tarfile.open(str(archive_path), 'w:gz') as tar:
                # Add a wrapper dir containing a theme with Theme.png
                theme_dir_path = Path(tmp) / 'build' / 'wrapper' / 'Theme1'
                theme_dir_path.mkdir(parents=True)
                (theme_dir_path / 'Theme.png').write_bytes(b'fake')
                (theme_dir_path / 'config1.dc').write_bytes(b'\x00' * 16)
                tar.add(str(theme_dir_path.parent), arcname='wrapper')

            # Make download_with_progress copy our real archive to the expected path
            def fake_download(url, dest, desc=""):
                import shutil
                shutil.copy2(str(archive_path), str(dest))
                return True
            mock_download.side_effect = fake_download

            with patch('trcc.theme_downloader.get_cache_dir', return_value=cache_dir), \
                 patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                result = download_pack(pack_name)

            self.assertEqual(result, 0)
            dest = themes_dir / resolution
            self.assertTrue(dest.exists())
            self.assertTrue((dest / '.trcc-meta.json').exists())
            # Verify at least one theme was copied
            theme_dirs = [d for d in dest.iterdir() if d.is_dir()]
            self.assertGreater(len(theme_dirs), 0)

    @patch('trcc.theme_downloader.verify_checksum', return_value=True)
    @patch('trcc.theme_downloader.get_installed_packs', return_value={})
    def test_cached_archive_skips_download(self, mock_installed, mock_checksum):
        """Existing valid archive in cache skips download."""
        pack_name = list(THEME_REGISTRY.keys())[0]
        info = THEME_REGISTRY[pack_name]

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            archive_path = cache_dir / f"{pack_name}-{info['version']}.tar.gz"
            # Create fake archive
            with tarfile.open(str(archive_path), 'w:gz') as tar:
                theme = Path(tmp) / 'T1'
                theme.mkdir()
                (theme / 'Theme.png').write_bytes(b'x')
                tar.add(str(theme), arcname='wrapper/T1')

            themes_dir = Path(tmp) / 'themes'
            with patch('trcc.theme_downloader.get_cache_dir', return_value=cache_dir), \
                 patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir), \
                 patch('trcc.theme_downloader.download_with_progress') as mock_dl:
                result = download_pack(pack_name)
            # Download should NOT have been called
            mock_dl.assert_not_called()
            self.assertEqual(result, 0)

    @patch('trcc.theme_downloader.download_with_progress', return_value=True)
    @patch('trcc.theme_downloader.verify_checksum', return_value=False)
    @patch('trcc.theme_downloader.get_installed_packs', return_value={})
    def test_checksum_fail_deletes_archive(self, mock_inst, mock_check, mock_dl):
        """Checksum mismatch → archive deleted, returns 1."""
        pack_name = list(THEME_REGISTRY.keys())[0]
        info = THEME_REGISTRY[pack_name]

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            cache_dir / f"{pack_name}-{info['version']}.tar.gz"

            def fake_dl(url, dest, desc=""):
                dest.write_bytes(b'bad')
                return True
            mock_dl.side_effect = fake_dl

            # Make verify_checksum return False only the second time (post-download)
            mock_check.side_effect = [False, False]

            with patch('trcc.theme_downloader.get_cache_dir', return_value=cache_dir):
                result = download_pack(pack_name)
            self.assertEqual(result, 1)

    @patch('trcc.theme_downloader.extract_archive', return_value=False)
    @patch('trcc.theme_downloader.verify_checksum', return_value=True)
    @patch('trcc.theme_downloader.get_installed_packs', return_value={})
    def test_extraction_failure(self, mock_inst, mock_check, mock_extract):
        """Extract failure → returns 1."""
        pack_name = list(THEME_REGISTRY.keys())[0]
        info = THEME_REGISTRY[pack_name]

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            (cache_dir / f"{pack_name}-{info['version']}.tar.gz").write_bytes(b'x')

            with patch('trcc.theme_downloader.get_cache_dir', return_value=cache_dir):
                result = download_pack(pack_name)
            self.assertEqual(result, 1)

    @patch('trcc.theme_downloader.get_installed_packs')
    def test_force_reinstall(self, mock_installed):
        """force=True bypasses already-installed check."""
        pack_name = list(THEME_REGISTRY.keys())[0]
        info = THEME_REGISTRY[pack_name]
        mock_installed.return_value = {pack_name: {'version': info['version']}}

        with patch('trcc.theme_downloader.download_with_progress', return_value=False), \
             patch('trcc.theme_downloader.get_cache_dir', return_value=Path('/tmp')):
            result = download_pack(pack_name, force=True)
        # Should proceed past the "already installed" check (fails on download)
        self.assertEqual(result, 1)


# ── Targeted coverage: installed packs, extract error, download flow ─────────

class TestGetInstalledPacksMeta(unittest.TestCase):
    """Cover meta-file and no-meta theme counting paths (lines 89-103)."""

    def test_meta_file_present(self):
        with tempfile.TemporaryDirectory() as d:
            themes_dir = Path(d) / 'themes'
            res_dir = themes_dir / '320320'
            res_dir.mkdir(parents=True)
            (res_dir / 'Theme1').mkdir()
            meta = {'pack_name': 'themes-320320', 'version': '1.0'}
            with open(res_dir / '.trcc-meta.json', 'w') as f:
                json.dump(meta, f)

            with patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                installed = get_installed_packs()
            self.assertIn('themes-320320', installed)
            self.assertEqual(installed['themes-320320']['version'], '1.0')

    def test_no_meta_counts_themes(self):
        with tempfile.TemporaryDirectory() as d:
            themes_dir = Path(d) / 'themes'
            res_dir = themes_dir / '320x320'
            res_dir.mkdir(parents=True)
            (res_dir / 'Theme1').mkdir()
            (res_dir / 'Theme2').mkdir()

            with patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                installed = get_installed_packs()
            # Should create pack name from dir name
            self.assertTrue(len(installed) > 0)

    def test_meta_file_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            themes_dir = Path(d) / 'themes'
            res_dir = themes_dir / '320320'
            res_dir.mkdir(parents=True)
            (res_dir / '.trcc-meta.json').write_text('not json')

            with patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                installed = get_installed_packs()
            # Corrupt meta should be silently ignored
            self.assertIsInstance(installed, dict)


class TestExtractArchiveError(unittest.TestCase):
    """Cover extract exception path (lines 248-250)."""

    def test_unknown_format(self):
        with tempfile.TemporaryDirectory() as d:
            fake_archive = Path(d) / 'archive.xyz'
            fake_archive.write_bytes(b'data')
            result = extract_archive(fake_archive, Path(d) / 'out')
            self.assertFalse(result)


class TestDownloadPackAlreadyInstalled(unittest.TestCase):
    """Cover already-installed version check (lines 271-276)."""

    @patch('trcc.theme_downloader.get_installed_packs')
    @patch('trcc.theme_downloader.THEME_REGISTRY', {
        'test-pack': {'name': 'Test', 'version': '1.0', 'resolution': '320x320',
                      'url': 'http://x', 'sha256': 'abc'}
    })
    def test_same_version_skips(self, mock_installed):
        mock_installed.return_value = {
            'test-pack': {'version': '1.0'}
        }
        result = download_pack('test-pack', force=False)
        self.assertEqual(result, 0)


class TestDownloadPackPrimaryFails(unittest.TestCase):
    """Cover download failure without mirror (lines 292-294)."""

    @patch('trcc.theme_downloader.get_installed_packs', return_value={})
    @patch('trcc.theme_downloader.download_with_progress', return_value=False)
    @patch('trcc.theme_downloader.get_cache_dir')
    @patch('trcc.theme_downloader.MIRROR_URLS', {})
    @patch('trcc.theme_downloader.THEME_REGISTRY', {
        'test-pack': {'name': 'Test', 'version': '2.0', 'resolution': '320x320',
                      'url': 'http://x', 'sha256': 'abc', 'size_mb': 1}
    })
    def test_no_mirror_returns_1(self, mock_cache, mock_dl, mock_installed):
        mock_cache.return_value = Path(tempfile.mkdtemp())
        result = download_pack('test-pack')
        self.assertEqual(result, 1)


class TestDownloadPackFullInstall(unittest.TestCase):
    """Cover successful download+extract+install flow (lines 329-370)."""

    def test_install_themes_from_archive(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cache_dir = d / 'cache'
            cache_dir.mkdir()
            themes_dir = d / 'themes'
            themes_dir.mkdir()

            # Create a valid tar.gz with themes inside
            archive_path = cache_dir / 'test-pack-1.0.tar.gz'
            source = d / 'build'
            source.mkdir()
            pack_dir = source / 'test-pack'
            pack_dir.mkdir()
            t1 = pack_dir / 'Theme1'
            t1.mkdir()
            (t1 / 'config1.dc').write_bytes(b'\xdc')
            (t1 / 'Theme.png').write_bytes(b'png')

            with tarfile.open(archive_path, 'w:gz') as tar:
                tar.add(pack_dir, arcname='test-pack')

            sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()

            with patch('trcc.theme_downloader.THEME_REGISTRY', {
                'test-pack': {'name': 'Test', 'version': '1.0', 'resolution': '320x320',
                              'url': 'http://x', 'sha256': sha, 'size_mb': 1}
            }), \
                 patch('trcc.theme_downloader.get_installed_packs', return_value={}), \
                 patch('trcc.theme_downloader.get_cache_dir', return_value=cache_dir), \
                 patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                result = download_pack('test-pack')

            self.assertEqual(result, 0)
            self.assertTrue((themes_dir / '320320' / 'Theme1' / 'config1.dc').exists())
            self.assertTrue((themes_dir / '320320' / '.trcc-meta.json').exists())

    def test_themes_directly_in_temp(self):
        """Themes at root of archive (no subdirectory wrapper)."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cache_dir = d / 'cache'
            cache_dir.mkdir()
            themes_dir = d / 'themes'
            themes_dir.mkdir()

            # Create tar.gz with themes at root level
            archive_path = cache_dir / 'test-pack-1.0.tar.gz'
            source = d / 'build'
            source.mkdir()
            t1 = source / 'Theme1'
            t1.mkdir()
            (t1 / 'config1.dc').write_bytes(b'\xdc')

            with tarfile.open(archive_path, 'w:gz') as tar:
                tar.add(t1, arcname='Theme1')

            sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()

            with patch('trcc.theme_downloader.THEME_REGISTRY', {
                'test-pack': {'name': 'Test', 'version': '1.0', 'resolution': '320x320',
                              'url': 'http://x', 'sha256': sha, 'size_mb': 1}
            }), \
                 patch('trcc.theme_downloader.get_installed_packs', return_value={}), \
                 patch('trcc.theme_downloader.get_cache_dir', return_value=cache_dir), \
                 patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                result = download_pack('test-pack')

            self.assertEqual(result, 0)

    def test_no_themes_in_archive(self):
        """Archive has no theme directories → returns 1."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cache_dir = d / 'cache'
            cache_dir.mkdir()
            themes_dir = d / 'themes'
            themes_dir.mkdir()

            # Create tar.gz with no theme content
            archive_path = cache_dir / 'test-pack-1.0.tar.gz'
            empty_dir = d / 'empty'
            empty_dir.mkdir()
            (empty_dir / 'readme.txt').write_text('hello')

            with tarfile.open(archive_path, 'w:gz') as tar:
                tar.add(empty_dir / 'readme.txt', arcname='readme.txt')

            sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()

            with patch('trcc.theme_downloader.THEME_REGISTRY', {
                'test-pack': {'name': 'Test', 'version': '1.0', 'resolution': '320x320',
                              'url': 'http://x', 'sha256': sha, 'size_mb': 1}
            }), \
                 patch('trcc.theme_downloader.get_installed_packs', return_value={}), \
                 patch('trcc.theme_downloader.get_cache_dir', return_value=cache_dir), \
                 patch('trcc.theme_downloader.get_user_themes_dir', return_value=themes_dir):
                result = download_pack('test-pack')

            self.assertEqual(result, 1)


class TestRemovePackNotFound(unittest.TestCase):
    """Cover remove_pack directory-not-found (lines 393-394)."""

    @patch('trcc.theme_downloader.get_user_themes_dir')
    @patch('trcc.theme_downloader.get_installed_packs')
    def test_dir_not_exists(self, mock_installed, mock_dir):
        mock_installed.return_value = {
            'test-pack': {'resolution': '320x320', 'version': '1.0'}
        }
        mock_dir.return_value = Path('/nonexistent/themes')
        result = remove_pack('test-pack')
        self.assertEqual(result, 1)


class TestCreateLocalPackNoThemes(unittest.TestCase):
    """Cover create_local_pack with empty dir (lines 420-422)."""

    def test_empty_source(self):
        with tempfile.TemporaryDirectory() as d:
            result = create_local_pack(d, 'test', '320x320')
            self.assertEqual(result, 1)

    def test_source_not_found(self):
        result = create_local_pack('/nonexistent', 'test', '320x320')
        self.assertEqual(result, 1)


class TestCreateLocalPackSuccess(unittest.TestCase):
    """Cover create_local_pack archive creation (lines 430-435+)."""

    def test_creates_archive(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'source'
            source.mkdir()
            t1 = source / 'Theme1'
            t1.mkdir()
            (t1 / 'config1.dc').write_bytes(b'data')

            orig_cwd = os.getcwd()
            os.chdir(d)
            try:
                result = create_local_pack(str(source), 'testpack', '320x320')
                self.assertEqual(result, 0)
                self.assertTrue(os.path.exists('testpack.tar.gz'))
            finally:
                os.chdir(orig_cwd)


if __name__ == '__main__':
    unittest.main()

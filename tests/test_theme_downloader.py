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
import struct
import sys
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


if __name__ == '__main__':
    unittest.main()

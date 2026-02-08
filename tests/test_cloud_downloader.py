"""Tests for cloud_downloader – cloud theme catalogue and download logic."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from trcc.cloud_downloader import (
    CATEGORIES,
    CATEGORY_NAMES,
    RESOLUTION_URLS,
    CloudThemeDownloader,
    get_known_themes,
    get_themes_by_category,
)

# ── Catalogue helpers ────────────────────────────────────────────────────────

class TestCatalogue(unittest.TestCase):

    def test_categories_has_all(self):
        prefixes = [c[0] for c in CATEGORIES]
        self.assertEqual(prefixes[0], 'all')

    def test_category_names_populated(self):
        self.assertIn('a', CATEGORY_NAMES)
        self.assertEqual(CATEGORY_NAMES['a'], 'Gallery')

    def test_get_known_themes_non_empty(self):
        themes = get_known_themes()
        self.assertGreater(len(themes), 50)
        self.assertTrue(themes[0].startswith('a'))

    def test_get_known_themes_format(self):
        for tid in get_known_themes():
            self.assertRegex(tid, r'^[a-z]\d{3}$')

    def test_get_themes_by_category_a(self):
        a_themes = get_themes_by_category('a')
        self.assertTrue(all(t.startswith('a') for t in a_themes))

    def test_get_themes_by_category_all(self):
        all_themes = get_themes_by_category('all')
        self.assertEqual(all_themes, get_known_themes())

    def test_get_themes_by_category_unknown(self):
        self.assertEqual(get_themes_by_category('z'), [])


# ── Resolution URLs ──────────────────────────────────────────────────────────

class TestResolutionURLs(unittest.TestCase):

    def test_common_resolutions_present(self):
        for res in ['240x240', '320x320', '480x480', '640x480']:
            self.assertIn(res, RESOLUTION_URLS)

    def test_url_format(self):
        self.assertEqual(RESOLUTION_URLS['320x320'], 'bj320320')


# ── CloudThemeDownloader init ────────────────────────────────────────────────

class TestDownloaderInit(unittest.TestCase):

    def test_default_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, 'home', return_value=Path(tmp)):
                dl = CloudThemeDownloader(resolution='320x320')
                self.assertIn('320320', str(dl.cache_dir))

    def test_custom_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            self.assertEqual(dl.cache_dir, Path(tmp))

    def test_base_url_contains_resolution(self):
        dl = CloudThemeDownloader(resolution='480x480', cache_dir='/tmp/test_trcc')
        self.assertIn('480480', dl.base_url)


# ── URL generation ───────────────────────────────────────────────────────────

class TestDownloaderURLs(unittest.TestCase):

    def setUp(self):
        self.dl = CloudThemeDownloader(resolution='320x320', cache_dir='/tmp/test_trcc')

    def test_get_theme_url(self):
        url = self.dl.get_theme_url('a001')
        self.assertTrue(url.endswith('a001.mp4'))

    def test_get_theme_url_strips_extension(self):
        url = self.dl.get_theme_url('a001.mp4')
        self.assertTrue(url.endswith('a001.mp4'))
        self.assertNotIn('.mp4.mp4', url)

    def test_get_preview_url(self):
        url = self.dl.get_preview_url('b005')
        self.assertTrue(url.endswith('b005.mp4'))


# ── Resolution / server switching ────────────────────────────────────────────

class TestDownloaderSwitching(unittest.TestCase):

    def test_set_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, 'home', return_value=Path(tmp)):
                dl = CloudThemeDownloader(resolution='320x320')
                dl.set_resolution('480x480')
                self.assertIn('480480', dl.base_url)
                self.assertIn('480480', str(dl.cache_dir))

    def test_set_server(self):
        dl = CloudThemeDownloader(resolution='320x320', cache_dir='/tmp/test_trcc')
        dl.set_server('china')
        self.assertIn('czhorde.com', dl.base_url)


# ── Cache operations ─────────────────────────────────────────────────────────

class TestDownloaderCache(unittest.TestCase):

    def test_is_cached_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            self.assertFalse(dl.is_cached('a001'))

    def test_is_cached_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            (Path(tmp) / 'a001.mp4').write_bytes(b'\x00')
            self.assertTrue(dl.is_cached('a001'))

    def test_get_cached_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            (Path(tmp) / 'b002.mp4').write_bytes(b'\x00')
            self.assertEqual(dl.get_cached_path('b002'), Path(tmp) / 'b002.mp4')

    def test_get_cached_themes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            (Path(tmp) / 'a001.mp4').write_bytes(b'\x00')
            (Path(tmp) / 'c010.mp4').write_bytes(b'\x00')
            cached = dl.get_cached_themes()
            self.assertEqual(cached, ['a001', 'c010'])

    def test_get_all_theme_ids(self):
        dl = CloudThemeDownloader(cache_dir='/tmp/test_trcc')
        self.assertEqual(dl.get_all_theme_ids(), get_known_themes())


# ── Download with mock network ───────────────────────────────────────────────

class TestDownloaderDownload(unittest.TestCase):

    def _mock_urlopen(self, data=b'\x00\x00\x01\x00'):
        """Build a mock urlopen context manager."""
        response = MagicMock()
        response.headers = {'content-length': str(len(data))}
        response.read.side_effect = [data, b'']
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        return response

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_theme_success(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen()

        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_theme('a001')
            assert result is not None
            self.assertTrue(Path(result).exists())

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_theme_returns_cached(self, mock_urlopen):
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            # Pre-create cached file
            cached = Path(tmp) / 'a001.mp4'
            cached.write_bytes(b'\xFF')

            result = dl.download_theme('a001')
            self.assertEqual(result, str(cached))
            mock_urlopen.assert_not_called()

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_preview_png(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(b'\x89PNG')

        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_preview_png('a001')
            self.assertIsNotNone(result)

    def test_download_theme_force_redownloads(self):
        """force=True should re-download even when cached."""
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            cached = Path(tmp) / 'a001.mp4'
            cached.write_bytes(b'\xFF')

            with patch('trcc.cloud_downloader.urlopen') as mock_urlopen:
                mock_urlopen.return_value = self._mock_urlopen()
                dl.download_theme('a001', force=True)
                mock_urlopen.assert_called_once()


# ── Cancel ───────────────────────────────────────────────────────────────────

class TestDownloaderCancel(unittest.TestCase):

    def test_cancel_sets_flag(self):
        dl = CloudThemeDownloader(cache_dir='/tmp/test_trcc')
        dl.cancel()
        self.assertTrue(dl._cancelled)


# ── Error handling in _download_file ─────────────────────────────────────────

class TestDownloaderErrorHandling(unittest.TestCase):

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_file_http_404(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            'http://test.com/a001.mp4', 404, 'Not Found', {}, None  # type: ignore[arg-type]
        )
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_theme('a999')
            self.assertIsNone(result)

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_file_http_500(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            'http://test.com/a001.mp4', 500, 'Server Error', {}, None  # type: ignore[arg-type]
        )
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_theme('a001')
            self.assertIsNone(result)

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_file_url_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError('Connection refused')
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_theme('a001')
            self.assertIsNone(result)

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_file_generic_exception(self, mock_urlopen):
        mock_urlopen.side_effect = OSError('Disk full')
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_theme('a001')
            self.assertIsNone(result)


# ── Download with progress ───────────────────────────────────────────────────

class TestDownloaderProgress(unittest.TestCase):

    def _mock_urlopen(self, data=b'\x00\x00\x01\x00'):
        response = MagicMock()
        response.headers = {'content-length': str(len(data))}
        response.read.side_effect = [data, b'']
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        return response

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_with_progress_callback(self, mock_urlopen):
        data = b'\x00' * 100
        mock_urlopen.return_value = self._mock_urlopen(data)
        progress_calls = []

        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            dl.download_theme('a001', on_progress=lambda d, t, p: progress_calls.append((d, t, p)))

        self.assertGreater(len(progress_calls), 0)
        # Last call should be 100%
        self.assertEqual(progress_calls[-1][2], 100)

    @patch('trcc.cloud_downloader.urlopen')
    def test_download_no_content_length(self, mock_urlopen):
        """When content-length is missing, download still succeeds."""
        response = MagicMock()
        response.headers = {}  # No content-length
        response.read.side_effect = [b'\x00' * 50, b'']
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response

        progress_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_theme('a001', on_progress=lambda d, t, p: progress_calls.append(p))

        self.assertIsNotNone(result)
        # No progress calls expected when content-length = 0
        self.assertEqual(len(progress_calls), 0)


# ── Download category and download_all ───────────────────────────────────────

class TestDownloaderCategory(unittest.TestCase):

    @patch.object(CloudThemeDownloader, 'download_theme')
    def test_download_category(self, mock_dl):
        mock_dl.return_value = '/tmp/test.mp4'
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            results = dl.download_category('a', max_themes=3)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(v == '/tmp/test.mp4' for v in results.values()))

    @patch.object(CloudThemeDownloader, 'download_theme')
    def test_download_category_with_progress(self, mock_dl):
        mock_dl.return_value = '/tmp/test.mp4'
        progress_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            dl.download_category(
                'a', max_themes=2,
                on_progress=lambda cur, total, tid: progress_calls.append((cur, total, tid))
            )
        self.assertEqual(len(progress_calls), 2)

    @patch.object(CloudThemeDownloader, 'download_theme')
    def test_download_category_cancel(self, mock_dl):
        """Cancel during iteration stops early."""
        def cancel_after_first(*args, **kwargs):
            dl._cancelled = True
            return '/tmp/test.mp4'
        mock_dl.side_effect = cancel_after_first
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            results = dl.download_category('a')
        # Should have downloaded only 1 theme before cancel kicked in
        self.assertEqual(len(results), 1)

    @patch.object(CloudThemeDownloader, 'download_category')
    def test_download_all(self, mock_cat):
        mock_cat.return_value = {'a001': '/tmp/a001.mp4'}
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            dl.download_all()
        mock_cat.assert_called_once_with('all', on_progress=None, force=False)

    @patch.object(CloudThemeDownloader, 'download_theme')
    def test_download_preview_delegates(self, mock_dl):
        mock_dl.return_value = '/tmp/a001.mp4'
        with tempfile.TemporaryDirectory() as tmp:
            dl = CloudThemeDownloader(cache_dir=tmp)
            result = dl.download_preview('a001')
        self.assertEqual(result, '/tmp/a001.mp4')


# ── Suffix stripping edge cases ───────────────────────────────────────────────

class TestSuffixStripping(unittest.TestCase):

    def test_get_cached_path_strips_mp4(self):
        """get_cached_path strips .mp4 suffix from theme_id."""
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        with patch.object(Path, 'exists', return_value=False):
            result = dl.get_cached_path('a001.mp4')
        self.assertIsNone(result)

    def test_download_preview_png_strips_suffix(self):
        """download_preview_png strips .png suffix."""
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        with patch.object(dl, '_download_file', return_value='/tmp/test.png') as mock_dl:
            with patch.object(Path, 'exists', return_value=False):
                dl.download_preview_png('a001.png')
        # Verify it constructed the URL without double .png
        if mock_dl.called:
            url = mock_dl.call_args[0][0]
            self.assertNotIn('.png.png', url)

    def test_download_theme_strips_mp4(self):
        """download_theme strips .mp4 suffix."""
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        with patch.object(dl, '_download_file', return_value='/tmp/a001.mp4'):
            with patch.object(Path, 'exists', return_value=False):
                result = dl.download_theme('a001.mp4')
        self.assertIsNotNone(result)


# ── Download failure and error paths ──────────────────────────────────────────

class TestDownloadErrors(unittest.TestCase):

    def test_download_theme_exception(self):
        """download_theme catches exceptions and returns None."""
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        with patch.object(dl, '_download_file', side_effect=RuntimeError("net error")):
            with patch.object(Path, 'exists', return_value=False):
                result = dl.download_theme('a001')
        self.assertIsNone(result)

    def test_download_file_http_error(self):
        """_download_file handles HTTPError with code."""
        from urllib.error import HTTPError
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        err = HTTPError('http://test.com', 404, 'Not Found', {}, None)
        with patch('trcc.cloud_downloader.urlopen', side_effect=err):
            result = dl._download_file('http://test.com/a.mp4', Path('/tmp/out.mp4'))
        self.assertIsNone(result)

    def test_download_file_url_error(self):
        """_download_file handles URLError."""
        from urllib.error import URLError
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        with patch('trcc.cloud_downloader.urlopen', side_effect=URLError('DNS failed')):
            result = dl._download_file('http://bad.host/a.mp4', Path('/tmp/out.mp4'))
        self.assertIsNone(result)

    def test_download_file_cancelled(self):
        """_download_file handles cancel during download."""
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        dl._cancelled = True
        response = MagicMock()
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.headers = {'Content-Length': '1000'}
        response.read.return_value = b'x' * 100
        with patch('trcc.cloud_downloader.urlopen', return_value=response):
            with patch.object(Path, 'exists', return_value=False):
                result = dl._download_file('http://test.com/a.mp4', Path('/tmp/out.mp4'))
        self.assertIsNone(result)


# ── Progress callback ────────────────────────────────────────────────────────

class TestDownloadProgress(unittest.TestCase):

    def test_progress_callback_called(self):
        """on_progress callback is invoked during download."""
        dl = CloudThemeDownloader(cache_dir='/tmp/test_cache')
        dl._cancelled = False

        response = MagicMock()
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.headers = {'content-length': '100'}
        # Return data then empty to end
        response.read.side_effect = [b'x' * 50, b'x' * 50, b'']

        progress_calls = []
        def on_progress(done, total, pct):
            progress_calls.append((done, total, pct))

        with patch('trcc.cloud_downloader.urlopen', return_value=response):
            with patch('builtins.open', unittest.mock.mock_open()):
                with patch.object(Path, 'exists', return_value=False):
                    with patch.object(Path, 'rename'):
                        with patch.object(Path, 'mkdir'):
                            dl._download_file(
                                'http://test.com/a.mp4',
                                Path('/tmp/out.mp4'),
                                on_progress=on_progress)

        self.assertGreater(len(progress_calls), 0)


# ── Convenience function ─────────────────────────────────────────────────────

class TestDownloadThemeConvenience(unittest.TestCase):

    @patch.object(CloudThemeDownloader, 'download_theme', return_value='/tmp/a001.mp4')
    def test_convenience_function(self, mock_dl):
        from trcc.cloud_downloader import download_theme
        result = download_theme('a001', resolution='320x320')
        self.assertEqual(result, '/tmp/a001.mp4')


if __name__ == '__main__':
    unittest.main()

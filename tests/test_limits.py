"""Where the library lives, and the 60-minute limit for recordings; no network."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul import audio, download, library, online


class LimitTests(unittest.TestCase):
    def test_tests_never_touch_the_real_library_data_or_cache(self):
        for place in (library.MUSIC_DIR, library.DATA_DIR, library.CACHE_DIR, library.DB_PATH.parent):
            self.assertTrue(place.is_relative_to(places.ROOT.resolve()), place)

    def test_the_places_are_the_macos_ones_unless_moved(self):
        import os, subprocess, sys
        env = {k: v for k, v in os.environ.items() if not k.startswith('PIANASOUL_')}
        found = subprocess.run([sys.executable, '-c', 'from pianasoul import library as l; print(l.MUSIC_DIR); '
                                'print(l.DATA_DIR); print(l.CACHE_DIR)'], env=env, capture_output=True, text=True,
                               check=True).stdout.split('\n')
        home = Path.home().resolve()
        self.assertEqual(found[:3], [str(home / 'Music/PianaSoul'), str(home / 'Library/Application Support/PianaSoul'),
                                     str(home / 'Library/Caches/PianaSoul')])

    def test_decoding_refuses_a_recording_over_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'three seconds.wav'
            with wave.open(str(path), 'wb') as clip:
                clip.setnchannels(1)
                clip.setsampwidth(2)
                clip.setframerate(8000)
                clip.writeframes(b'\x10\x00' * 8000 * 3)
            with patch.object(audio, 'MAX_AUDIO_S', 2), self.assertRaisesRegex(RuntimeError, 'longer than'):
                audio.load_audio(path, 8000)
            with patch.object(audio, 'MAX_AUDIO_S', 5):
                self.assertEqual(audio.load_audio(path, 8000).shape, (24000, 2))

    def test_a_long_video_is_refused_before_downloading(self):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = {'_type': 'video', 'duration': audio.MAX_AUDIO_S + 1}
        with tempfile.TemporaryDirectory() as tmp, patch('yt_dlp.YoutubeDL', return_value=ydl):
            with self.assertRaisesRegex(ValueError, 'longer than 60 minutes'):
                download.download_audio('https://www.youtube.com/watch?v=abcdefghijk', Path(tmp))
        ydl.process_ie_result.assert_not_called()

    def test_search_never_offers_a_video_over_the_limit(self):
        found = {'entries': [{'id': 'aaaaaaaaaaa', 'title': 'Nocturne', 'duration': 300},
                             {'id': 'bbbbbbbbbbb', 'title': 'All Nocturnes', 'duration': 7200}]}
        with patch.object(online, '_extract', return_value=found):
            self.assertEqual([r['video_title'] for r in online.search('nocturne')], ['Nocturne'])


if __name__ == '__main__':
    unittest.main()

"""She knows the user's library, to talk about it: what is there, what was played lately, the favorites, an artist
(with what the user wrote about them) or a playlist; and what she names there she may play."""
import json
from unittest.mock import Mock, patch

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.chat import TOOLS, Assistant
from pianasoul.playback import Playback
from test_app import Base


class HerLibraryTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Playback(self.lib, port=Mock())
        self.addCleanup(self.p.close)
        patch.object(Playback, 'play').start()
        patch.object(self.p, 'check_connection').start()
        self.addCleanup(patch.stopall)
        self.a = Assistant(self.lib, self.p)
        self.a.on_message = lambda message: None
        a, b, c = self.ids
        self.lib.execute("UPDATE recordings SET last_played_at = ? WHERE id = ?", ('2026-10-30T20:00:00+00:00', a))
        self.lib.execute("UPDATE recordings SET last_played_at = ? WHERE id = ?", ('2026-10-31T09:00:00+00:00', c))
        self.lib.execute("UPDATE recordings SET favorited_at = ? WHERE id = ?", ('2026-10-31T09:00:00+00:00', b))

    def ask(self, **arguments):
        return json.loads(self.a.run_tool('library_info', arguments))

    def test_an_overview_of_what_is_there(self):
        self.lib.new_playlist('睡前', self.ids[:2])
        seen = self.ask(about='overview')
        self.assertEqual(seen['pieces'], 3)
        self.assertEqual(seen['artists'][0], {'name': '周杰伦', 'pieces': 2})
        self.assertEqual(seen['playlists'], [{'name': '睡前', 'pieces': 2}])
        self.assertEqual((seen['favorites'], [p['piece'] for p in seen['played_last']]), (1, ['Nocturne', '晴天']))

    def test_what_was_played_lately_may_be_played_again(self):
        a, b, c = self.ids
        seen = self.ask(about='recently_played')
        self.assertEqual([p['id'] for p in seen['pieces']], [c, a], 'The last first')
        self.assertIn('played_at', seen['pieces'][0])
        self.assertNotIn('error', json.loads(self.a.run_tool('play_recording', {'id': c})))
        self.assertEqual([p['id'] for p in self.ask(about='favorites')['pieces']], [b])

    def test_an_artist_however_named_with_what_the_user_wrote(self):
        self.lib.set_artist_description('MappleZS', '弹得很温柔')
        seen = self.ask(about='artist', name='mapplezs')
        self.assertEqual((seen['name'], seen['description'], len(seen['as_pianist']), seen['as_artist']),
                         ('MappleZS', '弹得很温柔', 2, []))
        self.assertEqual(self.ask(about='artist', name='Liszt')['error'], 'artist_not_found')

    def test_a_playlist_with_its_description(self):
        pid = self.lib.new_playlist('睡前', self.ids[1:])['id']
        self.lib.edit_playlist(pid, '睡前', '慢一点的')
        seen = self.ask(about='playlist', name=' 睡前 ')
        self.assertEqual((seen['description'], [p['id'] for p in seen['pieces']]), ('慢一点的', self.ids[1:]))
        self.assertEqual(self.ask(about='playlist', name='早晨'), {'error': 'playlist_not_found', 'playlists': ['睡前']})

    def test_she_has_the_tool_and_it_changes_nothing(self):
        self.assertIn('library_info', {t['name'] for t in TOOLS})
        before = self.lib.all()
        self.ask(about='overview', name='x', extra='ignored')
        self.assertEqual(self.lib.all(), before)

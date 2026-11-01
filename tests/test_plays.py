"""How often a piece is heard: a play is half of it heard (30 s when its length is not known), counted once for each
listening; a pause or a resume is the same listening, another piece or this one again from its start another."""
from unittest.mock import Mock, patch

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.playback import Playback
from test_app import Base


class PlayCountTests(Base):
    def setUp(self):
        super().setUp()
        self.p = Playback(self.lib, port=Mock())
        self.addCleanup(self.p.close)
        patch.object(Playback, 'play').start()
        patch.object(self.p, 'check_connection').start()
        self.addCleanup(patch.stopall)

    def at(self, seconds, act, *args):
        with patch('pianasoul.playback.time.monotonic', return_value=seconds):
            act(*args)

    def pause_at(self, seconds, position):
        self.p._sent_to = position  # the notes sent so far, as the piano's thread would have it: where it goes on
        self.at(seconds, self.p.pause)

    def test_half_of_it_heard_is_one_play_however_often_paused(self):
        a = self.ids[0]
        self.at(0, self.p.select, a, [a])
        length = self.p.duration
        self.assertGreater(length, 0)
        self.pause_at(length * .3, length * .3)
        self.assertEqual(self.lib.play_counts(), {}, 'Not yet half')
        self.at(100, self.p.toggle)  # on from where it was
        self.pause_at(100 + length * .3, length * .6)
        self.assertEqual(self.lib.play_counts(), {a: 1})
        self.at(200, self.p.toggle)
        self.pause_at(200 + length * .3, length * .9)
        self.assertEqual(self.lib.play_counts(), {a: 1}, 'The same listening, counted once')

    def test_again_from_its_start_is_another_play_and_a_skip_is_none(self):
        a, b, c = self.ids
        self.at(0, self.p.select, a, [a, b])
        self.at(self.p.duration, self.p.select, a, [a, b])  # heard through, then again from its start
        self.at(self.p.duration * 2, self.p.select, b, [a, b])
        self.at(self.p.duration * 2 + self.p.duration * .05, self.p.select, c, [c])  # b skipped at once
        self.assertEqual(self.lib.play_counts(), {a: 2})
        self.assertTrue(self.lib.first_play())
        self.assertEqual(self.lib.play_counts(since='2999-01-01'), {}, 'Counted from a time on')

    def test_each_loop_of_one_is_a_play_and_a_seek_to_its_start_is_not_another(self):
        a = self.ids[0]
        self.at(0, self.p.select, a, [a])
        length = self.p.duration
        for loop in (1, 2):  # the piece over, it plays again after the gap (repeat one)
            self.at(loop * (length + 5), self.p.start, [self.p.current], -2.0)
        self.at(3 * (length + 5), self.p.pause)
        self.assertEqual(self.lib.play_counts(), {a: 3})
        b = self.ids[1]
        self.at(1000, self.p.select, b, [b])
        self.at(1000 + self.p.duration * .4, self.p.seek, 0.0)  # back to its start: the same listening
        self.at(1000 + self.p.duration * .8, self.p.pause)
        self.assertEqual(self.lib.play_counts().get(b), 1)

    def test_after_a_stop_a_seek_starts_a_new_listening_and_after_a_pause_it_goes_on(self):
        a, b = self.ids[:2]
        self.at(0, self.p.select, a, [a])
        length = self.p.duration
        self.at(length, self.p.stop)  # heard through: one play
        self.at(length + 1, self.p.seek, 1.0)
        self.at(length + 2, self.p.toggle)
        self.pause_at(length * 2 + 2, length)  # all of it again
        self.assertEqual(self.lib.play_counts(), {a: 2})
        self.at(1000, self.p.select, b, [b])
        self.pause_at(1000 + self.p.duration * .4, self.p.duration * .4)
        self.at(1001, self.p.seek, 0.0)  # back to its start while paused, then on: the same listening
        self.at(1002, self.p.toggle)
        self.pause_at(1002 + self.p.duration * .2, self.p.duration * .2)
        self.assertEqual(self.lib.play_counts().get(b), 1)

    def test_the_gap_before_a_piece_is_not_heard(self):
        a = self.ids[0]
        self.at(0, self.p.select, a, [a])
        length = self.p.duration
        self.at(length * .2, self.p.start, [self.p.current], -length * .4)  # a long gap before it sounds
        self.at(length * .2 + length * .45, self.p.pause)  # gone before half of it was heard
        self.assertEqual(self.lib.play_counts(), {})

    def test_a_piece_of_unknown_length_is_a_play_after_30_seconds(self):
        a = self.ids[0]
        self.lib.execute("UPDATE recordings SET duration_s = 0 WHERE id = ?", (a,))
        self.at(0, self.p.select, a, [a])
        self.pause_at(29, 29)
        self.assertEqual(self.lib.play_counts(), {})
        self.at(40, self.p.toggle)
        self.pause_at(42, 31)
        self.assertEqual(self.lib.play_counts(), {a: 1})

    def test_a_deleted_piece_takes_its_plays_along(self):
        a = self.ids[0]
        self.lib.heard(a)
        with patch('pianasoul.library.send2trash'):
            self.lib.delete(a)
        self.assertEqual(self.lib.play_counts(), {})

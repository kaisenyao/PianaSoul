"""One artist, one name: however a new piece writes an artist's name, the library keeps the one it has (artist_key),
and names written differently before are made one when it opens, their pictures and descriptions with them."""
from pathlib import Path
from unittest.mock import Mock, patch

import places  # noqa: F401  (first: the library, data and cache folders are throwaway ones)
from pianasoul.downloads import Downloads
from pianasoul.library import Library, artist_key
from test_app import Base
from test_library import make_midi

JPEG = b'\xff\xd8\xff\xe0' + b'picture' * 10


class ArtistNameTests(Base):
    def test_one_artist_however_written(self):
        for a, b in (('AC/DC', 'ac_dc'), ('Adele', ' adele '), ('Dvořák', 'Dvorak'), ('ＪＪ Lin', 'jj lin'), ('周杰伦', '周 杰伦')):
            self.assertEqual(artist_key(a), artist_key(b), (a, b))
        self.assertNotEqual(artist_key('周杰伦'), artist_key('周杰倫'), 'Another language or script is for the model')
        self.assertEqual(artist_key('...'), '')

    def test_a_name_the_library_has_is_kept_as_it_has_it(self):
        self.assertEqual(self.lib.artist_names()[:2], ['MappleZS', '周杰伦'], 'From the titles too, the most pieces first')
        self.assertEqual(self.lib.known_artist(' mapplezs '), 'MappleZS')
        self.assertEqual(self.lib.known_artist('Liszt'), 'Liszt', 'Someone new keeps their name')
        self.assertEqual(self.lib.known_artist('...'), '...')

    def test_an_artist_written_anew_is_so_everywhere_and_another_keeps_the_librarys_name(self):
        a, b, c = self.ids
        self.lib.set_artist_description('MappleZS', '钢琴博主')
        self.assertEqual(self.lib.edit(a, '晴天', '周杰伦', 'Mapple ZS')['title'], '周杰伦 - 晴天 (Mapple ZS)')
        self.assertEqual(self.lib.get(b)['title'], '周杰伦 - 搁浅 (Mapple ZS)', 'The same pianist, written so on all theirs')
        self.assertEqual(self.lib.descriptions(['Mapple ZS']), {'Mapple ZS': '钢琴博主'}, 'With what was written of them')
        self.assertEqual(self.lib.edit(c, 'Nocturne', 'Chopin', 'mapple zs')['performer'], 'Mapple ZS',
                         'Another artist for this piece: the one the library has')

    def test_a_title_that_does_not_come_apart_into_its_names_is_left_as_it_is(self):
        odd = self.lib.add(make_midi(self.root / 'Music' / 'y.mid', note=71), 'mapplezs - (Live)')['id']
        bare = self.lib.add(make_midi(self.root / 'Music' / 'z.mid', note=72), 'Hello', performer='mapplezs')['id']
        again = Library(self.root / 'library.db', self.root / 'Music')
        self.addCleanup(again.close)
        self.assertEqual(again.get(odd)['title'], 'mapplezs - (Live)')
        self.assertEqual((again.get(bare)['title'], again.get(bare)['performer']), ('Hello', 'MappleZS'), 'Its field only')

    def test_the_model_saying_no_artist_leaves_none(self):
        from pianasoul.library import enrich
        lone = self.lib.add(make_midi(self.root / 'Music' / 'w.mid', note=73), 'Gymnopédie')['id']
        enrich(self.lib, lambda prompt: {str(lone): {'artist': None, 'aliases': None, 'tags': None}})
        self.assertEqual(self.lib.get(lone)['composer'], '')
        enrich(self.lib, lambda prompt: {})  # named once: not asked again
        other = self.lib.add(make_midi(self.root / 'Music' / 'v.mid', note=76), 'Nocturne 2')['id']
        enrich(self.lib, lambda prompt: {str(other): {'artist': 'chopin'}})
        self.assertEqual(self.lib.get(other)['composer'], 'Chopin', "The library's name for them")

    def test_names_written_differently_before_are_made_one_with_their_picture_and_description(self):
        lower = self.lib.add(make_midi(self.root / 'Music' / 'x.mid', note=70), 'mapplezs - 稻香')['id']
        self.lib.set_artist_picture('mapplezs', JPEG)
        self.lib.set_artist_description('mapplezs', '弹得很温柔')
        self.lib.set_artist_description('MappleZS', '钢琴博主')
        self.lib.set_artist_description('Chopin', 'x' * 1000)
        self.lib.set_artist_description('chopin', 'y' * 1000)
        self.lib.add(make_midi(self.root / 'Music' / 'u.mid', note=77), 'chopin - Ballade')
        again = Library(self.root / 'library.db', self.root / 'Music')
        self.addCleanup(again.close)
        self.assertEqual(again.get(lower)['title'], 'MappleZS - 稻香')
        self.assertNotIn('mapplezs', again.artist_names())
        self.assertEqual(again.artist_picture('MappleZS').read_bytes(), JPEG, 'Its picture goes along')
        self.assertEqual(again.descriptions(['MappleZS', 'mapplezs']), {'MappleZS': '钢琴博主\n\n弹得很温柔'}, 'Nothing lost')
        self.assertEqual(len(again.descriptions(['Chopin'])['Chopin']), 1000, 'As much as one holds')


class NewPieceTests(Base):
    def test_a_new_piece_takes_the_librarys_name_for_its_artist(self):
        audio = self.root / 'b.mp3'
        audio.write_bytes(b'x')
        asked = []
        convert = lambda engine, audio, out, options, output_name: {'files': {'midi': str(make_midi(Path(out) / 'x.mid', note=75))}}
        with patch('pianasoul.pipeline.convert_file', side_effect=convert):
            d = Downloads(self.lib, engine=Mock(), name_piece=lambda query, video, artists: asked.append(artists) or
                                         {'piece': '稻香', 'artist': '周杰伦', 'performer': 'mapplezs'})
            rec = d.run(str(audio))
        self.assertIn('周杰伦', asked[0], 'The model is told who is there already')
        self.assertEqual((rec['title'], rec['performer']), ('周杰伦 - 稻香 (MappleZS)', 'MappleZS'))

    def test_the_model_is_told_the_artists_there_are(self):
        from pianasoul import core
        with patch('pianasoul.llm.complete_json', return_value={'piece': '稻香', 'artist': '周杰伦'}) as model:
            core.name_piece('Jay Chou rice field', {'video_title': 'Jay Chou - Rice Field piano'}, ['周杰伦'])
        self.assertIn('"周杰伦"', model.call_args.args[0])

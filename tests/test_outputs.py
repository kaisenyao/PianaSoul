"""MIDI outputs: listed by CoreMIDI, chosen by unique ID, opened afresh, switched while running with no note left
sounding, and a short arpeggio to hear the piano. The fakes stand for CoreMIDI; one test plays on a real CoreMIDI
output this test makes for itself, as an app such as Pianoteq does."""
import contextlib
import ctypes
import dataclasses
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import mido

import places
from pianasoul import play
from pianasoul.core import Core
from pianasoul.library import Library
from pianasoul.play import Output
from pianasoul.playback import Playback
from test_library import make_midi

PIANO = Output(-1443376940, 'FP-40 MIDI Bluetooth', 'bluetooth', True, None, 11)
USB = Output(1060493295, 'Roland Digital Piano', 'usb', True, None, 12)
SESSION = Output(-1285932052, 'Network pianasoul', 'network', True, ('iPad',), 13)


def notes_off(port):
    return [c.args[0] for c in port.send.call_args_list if c.args[0].type == 'control_change'
            and c.args[0].control == 123]


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.lib = Library(root / 'library.db', root / 'Music')
        self.addCleanup(self.lib.close)
        self.song = make_midi(root / 'Music' / 'Song.mid')
        self.outputs, self.opened = [PIANO, USB, SESSION], []

        def open_output(out):
            self.opened.append(Mock(output=out))
            return self.opened[-1]
        for patcher in (patch('pianasoul.play.outputs', side_effect=lambda: list(self.outputs)),
                        patch('pianasoul.play.output', side_effect=lambda i: next((o for o in self.outputs
                                                                                   if o.id == i), None)),
                        patch('pianasoul.play.CoreMidiOut', side_effect=open_output),
                        patch.object(Playback, 'play'), contextlib.redirect_stdout(io.StringIO())):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)
        self.p = Playback(self.lib)
        self.addCleanup(self.p.close)

    def test_the_output_chosen_is_known_by_its_id_not_its_name(self):
        self.p.choose(USB)
        twin = dataclasses.replace(USB, id=99, endpoint=19)  # another piano of the same model
        self.outputs.insert(0, twin)
        self.p.check_connection()
        self.assertEqual(self.opened[-1].output.id, USB.id)
        self.assertEqual(json.loads(self.lib.setting('output')), {'id': USB.id, 'name': 'Roland Digital Piano', 'kind': 'usb'})

    def test_one_that_is_offline_is_waited_for_never_swapped_for_its_namesake(self):
        self.p.choose(USB)
        self.outputs[:] = [dataclasses.replace(USB, online=False), dataclasses.replace(USB, id=99)]
        with self.assertRaisesRegex(LookupError, '"Roland Digital Piano" is not connected'):
            self.p.check_connection()
        self.assertEqual(self.opened, [])
        self.outputs[0] = USB  # back
        self.p.check_connection()
        self.assertEqual(self.opened[-1].output.id, USB.id)

    def test_a_new_id_is_taken_only_when_the_old_one_is_gone_and_one_output_has_its_name(self):
        pianoteq = Output(5, 'Pianoteq 9', 'virtual', True, None, 15)
        self.outputs.append(pianoteq)
        self.p.choose(pianoteq)
        restarted = dataclasses.replace(pianoteq, id=6)  # the app started again, with a new ID
        self.outputs[-1] = restarted
        self.p.check_connection()
        self.assertEqual(self.opened[-1].output.id, 6)
        self.assertEqual(json.loads(self.lib.setting('output'))['id'], 6, 'Kept from now on')
        self.outputs.append(dataclasses.replace(pianoteq, id=7, endpoint=17))  # and a second one of that name
        self.outputs.remove(restarted)
        self.outputs.append(dataclasses.replace(pianoteq, id=8, endpoint=18))
        with self.assertRaisesRegex(LookupError, '"Pianoteq 9" is not there'):
            self.p.check_connection()

    def test_the_lead_follows_the_output(self):
        for out, lead in ((USB, 0.0), (PIANO, 0.0), (SESSION, 1.0)):
            self.p.choose(out)
            self.p.check_connection()
            self.assertEqual(self.p.lead, lead, out.kind)
        with patch.dict(os.environ, {'PIANASOUL_LEAD': '0.25'}):
            self.p.choose(USB)
            self.p.check_connection()
        self.assertEqual(self.p.lead, 0.25, 'PIANASOUL_LEAD sets it for all')

    def test_switching_while_playing_pauses_there_and_leaves_no_note_sounding(self):
        self.p.choose(USB)
        rec = self.lib.add(self.song, 'Song')
        self.p.select(rec['id'])
        self.p._offset = 0
        old = self.p.port
        self.p.state, self.p._sent_to = 'playing', 1.5  # as if a second and a half in
        self.p.choose(PIANO)
        self.assertEqual((self.p.state, self.p.position), ('paused', 1.5))
        self.assertEqual(len(notes_off(old)), 16, 'All Notes Off on every channel of the old output')
        old.close.assert_called_once()
        self.assertIsNone(self.p.port)
        self.p.toggle()
        self.assertEqual(self.p.port.output.id, PIANO.id, 'The next notes go to the new one')

    def test_closing_turns_every_note_off_first(self):
        self.p.choose(USB)
        self.p.check_connection()
        port = self.p.port
        self.p.close()
        self.assertEqual(len(notes_off(port)), 16)
        port.close.assert_called_once()

    def test_a_short_arpeggio_at_the_volume_set(self):
        self.p.choose(USB)
        self.p.volume = 50
        started = time.perf_counter()
        self.p.test_tone()
        self.assertLess(time.perf_counter() - started, 0.1, 'Not waited for: it plays on its own')
        self.p._thread.join()
        sent = [c.args[0] for c in self.p.port.send.call_args_list]
        self.assertEqual([m.note for m in sent if m.type == 'note_on'], [60, 64, 67, 72])
        self.assertEqual([m.note for m in sent if m.type == 'note_off'][:4], [60, 64, 67, 72], 'And let go')
        self.assertEqual({m.value for m in sent if m.type == 'control_change' and m.control == 7}, {64})
        self.p.state = 'playing'
        with self.assertRaises(RuntimeError):
            self.p.test_tone()
        self.p.state = 'stopped'
        self.outputs.clear()
        with self.assertRaises(LookupError):
            self.p.test_tone()


    def test_on_a_network_session_the_arpeggio_leaves_as_a_piece_does_one_lead_ahead_in_turn(self):
        self.p.choose(SESSION)
        left = []
        port = Mock(output=SESSION)
        port.send.side_effect = lambda msg, when=None: left.append((time.perf_counter(), msg, when))
        with patch('pianasoul.play.CoreMidiOut', return_value=port):
            self.p.test_tone()
            self.p._thread.join()
        notes = [(sent, msg, when) for sent, msg, when in left if msg.type in {'note_on', 'note_off'}]
        self.assertEqual([m.note for _, m, _ in notes if m.type == 'note_on'], [60, 64, 67, 72])
        first = notes[0][2]
        self.assertEqual([round(when - first, 2) for _, _, when in notes], [0, .15, .3, .45, 1.2, 1.2, 1.2, 1.2],
                         'Each with the time it sounds')
        for sent, msg, when in notes:
            self.assertAlmostEqual(when - sent, 1.0, delta=0.05, msg=f'{msg}: leaves one lead early, not all at once')

    def test_choosing_another_output_while_the_test_sounds_stops_the_test_first(self):
        failed = []
        hook, threading.excepthook = threading.excepthook, lambda args: failed.append(args.exc_value)
        self.addCleanup(setattr, threading, 'excepthook', hook)
        self.p.choose(SESSION)
        self.p.test_tone()
        thread = self.p._thread
        time.sleep(.05)
        self.p.choose(USB)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failed, [], 'The test gave way; it did not send on the output closed')

    def test_paused_on_a_network_session_a_setting_does_not_move_where_it_stopped(self):
        # The volume and All Notes Off carry a time too, but they are not music: a piece paused at 1:00 shows 1:00
        # while the volume is dragged, and after the output is changed.
        self.p.choose(SESSION)
        self.p.check_connection()
        self.p.state, self.p._offset = 'paused', 60.0
        self.p.set_volume(30)
        self.assertEqual(self.p.snapshot()['position'], 60.0)
        self.p.choose(USB)
        self.assertEqual(self.p.snapshot()['position'], 60.0)

    def test_on_a_network_session_nothing_is_sent_for_at_once(self):
        # An iPad plays a note stamped for later at once when it arrives with a message stamped for now (recorded
        # M3): so the volume, All Notes Off when the output is opened again, and a volume change carry a time too.
        self.p.choose(SESSION)
        left = []
        port = Mock(output=SESSION)
        port.send.side_effect = lambda msg, when=None: left.append((time.perf_counter(), msg, when))
        with patch('pianasoul.play.CoreMidiOut', return_value=port):
            for _ in range(2):  # the second opens the output afresh: All Notes Off on the old one first
                self.p.test_tone()
                self.p._thread.join()
                self.p._last_when = 0.0  # as long after as it takes to open it afresh
            self.p.set_volume(40)
        self.assertTrue(notes_off(port), 'the old output was closed')
        for sent, msg, when in left:
            self.assertIsNotNone(when, f'{msg}: sent for at once')
            self.assertGreaterEqual(when - sent, 0.95, f'{msg}: stamped one lead ahead at least')
        volume = next(when for _, msg, when in left if msg.type == 'control_change' and msg.control == 7)
        first = next(when for _, msg, when in left if msg.type == 'note_on')
        self.assertLessEqual(volume, first, 'The volume comes with the first note, not after it')


class OutputsApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from pianasoul import server
        from test_server import ORIGIN, signed_in
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        lib = Library(root / 'library.db', root / 'Music')
        self.addCleanup(lib.close)
        self.outputs, self.opened = [USB, SESSION], []

        def open_output(out):
            self.opened.append(Mock(output=out))
            return self.opened[-1]
        for patcher in (patch('pianasoul.play.outputs', side_effect=lambda: list(self.outputs)),
                        patch('pianasoul.play.output', side_effect=lambda i: next((o for o in self.outputs
                                                                                   if o.id == i), None)),
                        patch('pianasoul.play.CoreMidiOut', side_effect=open_output),
                        contextlib.redirect_stdout(io.StringIO())):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)
        self.core = Core(lib=lib, assistant=False)
        self.addCleanup(self.core.close)
        with_core = patch.object(server, 'core', self.core)
        with_core.start()
        self.addCleanup(with_core.stop)
        self.client = signed_in(TestClient(server.app, headers=ORIGIN))

    def test_every_output_with_its_kind_and_the_one_chosen(self):
        found = self.client.get('/api/outputs').json()
        self.assertEqual(found, [
            {'id': USB.id, 'name': 'Roland Digital Piano', 'kind': 'usb', 'connected': True, 'chosen': False},
            {'id': SESSION.id, 'name': 'Network pianasoul', 'kind': 'network', 'connected': True, 'peers': ['iPad'],
             'chosen': True}], 'Until one is chosen: the one named like PIANASOUL_PORT')

    def test_choosing_takes_effect_at_once_and_is_kept(self):
        answer = self.client.patch('/api/settings', json={'output': USB.id}).json()
        self.assertEqual(answer['output'], USB.id)
        self.assertEqual([o['chosen'] for o in self.client.get('/api/outputs').json()], [True, False])
        self.assertEqual(self.client.get('/api/state').json()['devices'],
                         [{'name': 'Roland Digital Piano', 'kind': 'usb', 'connected': True}])
        self.assertEqual(self.client.patch('/api/settings', json={'output': 12345}).json()['output'], USB.id,
                         'Only an output this Mac has')

    def test_the_page_is_told_whenever_the_piano_comes_or_goes(self):
        import threading
        from pianasoul import core as core_module
        told, heard = [], threading.Event()
        self.core.listeners.append(lambda e: e['type'] == 'piano' and (told.append(e['piano']), heard.set()))
        self.client.patch('/api/settings', json={'output': USB.id})
        self.assertTrue(heard.wait(2))
        self.assertEqual(self.client.get('/api/state').json()['piano'],
                         {'name': 'Roland Digital Piano', 'kind': 'usb', 'connected': True})
        heard.clear()
        self.outputs[0] = dataclasses.replace(USB, online=False)  # unplugged
        self.core.look_at_piano()  # as CoreMIDI's change does
        self.assertTrue(heard.wait(2))
        self.assertEqual(told[-1], {'name': 'Roland Digital Piano', 'kind': 'usb', 'connected': False})
        heard.clear()
        del self.outputs[0]  # gone from the Mac altogether: still the one chosen, not connected
        with patch.object(core_module, 'PIANO_SAFETY_S', .1):
            self.core.look_at_piano()
            self.assertFalse(heard.wait(.5), 'Nothing new to tell')
        self.outputs.insert(0, USB)
        self.core.look_at_piano()
        self.assertTrue(heard.wait(2))
        self.assertTrue(told[-1]['connected'])

    def test_a_network_session_is_looked_at_every_few_seconds(self):
        import threading
        from pianasoul import core as core_module
        heard = threading.Event()
        self.core.listeners.append(lambda e: e['type'] == 'piano' and heard.set())
        self.client.patch('/api/settings', json={'output': SESSION.id})
        heard.wait(2)
        heard.clear()
        with patch.object(core_module, 'PIANO_LOOK_S', .2):
            self.core.look_at_piano()  # the next waits are short ones
            time.sleep(.3)
            self.outputs[1] = dataclasses.replace(SESSION, peers=())  # the iPad left: CoreMIDI says nothing
            self.assertTrue(heard.wait(2), 'Found by looking, not told')
        self.assertFalse(self.core.piano['connected'])

    def test_the_test_arpeggio_or_why_not(self):
        self.client.patch('/api/settings', json={'output': USB.id})
        self.assertEqual(self.client.post('/api/outputs/test').json(), {'ok': True})
        self.outputs[0] = dataclasses.replace(USB, online=False)
        answer = self.client.post('/api/outputs/test')
        self.assertEqual((answer.status_code, answer.json()),
                         (409, {'error': 'piano', 'why': '"Roland Digital Piano" is not connected'}))


@unittest.skipUnless(sys.platform == 'darwin', 'CoreMIDI')
class CoreMidiTests(unittest.TestCase):
    """On a real CoreMIDI output: a virtual one this test makes, which receives what is played on it."""

    def setUp(self):
        from ctypes import CFUNCTYPE, POINTER, byref, c_uint32, c_void_p
        cm = play._coremidi()
        self.received, self.lock = [], threading.Lock()

        def read(packets, _ref, _source):  # a MIDIPacketList, packed to 4 bytes
            count = ctypes.c_uint32.from_address(packets).value
            at = packets + 4
            for _ in range(count):
                length = ctypes.c_uint16.from_address(at + 8).value
                data = ctypes.string_at(at + 10, length)
                with self.lock:
                    self.received.append(bytes(data))
                at = (at + 10 + length + 3) & ~3
        self.read = CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)(read)  # kept, while CoreMIDI may call it
        cm.MIDIDestinationCreate.argtypes = [c_uint32, c_void_p, c_void_p, c_void_p, POINTER(c_uint32)]
        cm.MIDIDestinationCreate.restype = ctypes.c_int32
        cm.MIDIEndpointDispose.argtypes = [c_uint32]
        self.name = f'PianaSoul test {os.getpid()}'
        endpoint = c_uint32()
        self.assertEqual(cm.MIDIDestinationCreate(play.client(), play._cfstring(self.name), self.read, None,
                                                  byref(endpoint)), 0)
        self.addCleanup(cm.MIDIEndpointDispose, endpoint)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lib = Library(Path(tmp.name) / 'library.db', Path(tmp.name) / 'Music')
        self.addCleanup(self.lib.close)
        real = patch('pianasoul.play.CoreMidiOut', places.REAL_MIDI_OUT)
        real.start()
        self.addCleanup(real.stop)

    def messages(self, count, within=3.0):
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.received) >= count:
                    break
            time.sleep(0.02)
        parser = mido.Parser()  # a packet may hold several messages
        with self.lock:
            for data in self.received:
                parser.feed(data)
        return list(parser)

    def test_coremidi_tells_when_an_output_comes_or_goes(self):
        import threading
        told = threading.Event()
        play.on_change(told.set)
        self.addCleanup(play._changes.remove, told.set)
        from ctypes import byref, c_uint32
        cm, other = play._coremidi(), c_uint32()
        cm.MIDIDestinationCreate(play.client(), play._cfstring(self.name + ' 2'), self.read, None, byref(other))
        self.assertTrue(told.wait(3), 'One came')
        told.clear()
        cm.MIDIEndpointDispose(other)
        self.assertTrue(told.wait(3), 'One went')

    def test_listed_chosen_and_played_on(self):
        mine = next(o for o in play.outputs() if o.name == self.name)
        self.assertEqual((mine.kind, mine.connected, mine.peers), ('virtual', True, None), 'An app\'s own output')
        self.assertEqual(play.output(mine.id), mine)
        p = Playback(self.lib)
        p.choose(mine)
        p.volume = 100
        p.test_tone()
        p._thread.join()
        heard = self.messages(16 + 8)
        self.assertEqual([m.note for m in heard if m.type == 'note_on'], [60, 64, 67, 72])
        self.assertEqual(len([m for m in heard if m.type == 'note_off']), 4)
        self.assertEqual({m.value for m in heard if m.type == 'control_change' and m.control == 7}, {127})
        with self.lock:
            self.received.clear()
        p.close()
        self.assertEqual(len([m for m in self.messages(16) if m.type == 'control_change' and m.control == 123]), 16)


if __name__ == '__main__':
    unittest.main()

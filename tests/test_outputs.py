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
        self.assertEqual(json.loads(self.lib.setting('output')), {'id': USB.id, 'name': 'Roland Digital Piano'})

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
        self.p.test_tone()
        sent = [c.args[0] for c in self.p.port.send.call_args_list]
        self.assertEqual([m.note for m in sent if m.type == 'note_on'], [60, 64, 67, 72])
        self.assertEqual({m.value for m in sent if m.type == 'control_change' and m.control == 7}, {64})
        self.p.state = 'playing'
        with self.assertRaises(RuntimeError):
            self.p.test_tone()
        self.p.state = 'stopped'
        self.outputs.clear()
        with self.assertRaises(LookupError):
            self.p.test_tone()


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

    def test_listed_chosen_and_played_on(self):
        mine = next(o for o in play.outputs() if o.name == self.name)
        self.assertEqual((mine.kind, mine.connected, mine.peers), ('virtual', True, None), 'An app\'s own output')
        self.assertEqual(play.output(mine.id), mine)
        p = Playback(self.lib)
        p.choose(mine)
        p.volume = 100
        p.test_tone()
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

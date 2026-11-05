"""A piece played while it is transcribed (Downloads). Its notes come in parts (pipeline.Transcription), each with
every note that starts before a time, the last with the rest. It plays message for message as its MIDI file will (as
mido reads the file pretty_midi writes, which Player plays), and where its notes are not there yet its music waits
for them."""

from __future__ import annotations

import bisect
import math
import threading

import mido
import pretty_midi

from .cleanup import RESOLUTION, TEMPO

PLAY_AHEAD_S = 4.0  # it plays once this much is there past its first note: its transcription stays ahead from then on
CONTROL, NOTE = 8 << 16, 10 << 16  # how pretty_midi orders a tick's messages: controls, then notes by pitch, offs first


class NotMade(Exception):
    """The piece will not be made after all: its transcription failed, or was stopped."""


class Growing:
    """One piece being made, as much of it as there is: its messages, and its notes for its picture."""

    def __init__(self):
        self._clock = pretty_midi.PrettyMIDI(resolution=RESOLUTION, initial_tempo=TEMPO)  # times to ticks, as written
        self._messages: list[tuple[int, int, mido.Message]] = []  # (tick, order, message), all there so far, in order
        self._ready = 0  # how many of them may be played: those before any a part to come can bring
        self._notes: list[list] = []  # [start, length, pitch, velocity], for the piece's picture
        self.until = 0.0  # every note that starts before this is there; math.inf once all of it is
        self.failed = False
        self.listeners: list = []  # called when more of it is there, or it failed (on the thread that said so)
        self._cond = threading.Condition()

    def add(self, midi: pretty_midi.PrettyMIDI, until: float) -> None:
        """A part: its notes and controls (the pedal), every note that starts before until."""
        tick, made, notes = self._clock.time_to_tick, [], []
        for piano in midi.instruments:
            for n in piano.notes:
                made.append((tick(n.start), NOTE + (n.pitch << 8) + n.velocity,
                             mido.Message("note_on", note=n.pitch, velocity=n.velocity)))
                made.append((tick(n.end), NOTE + (n.pitch << 8), mido.Message("note_on", note=n.pitch, velocity=0)))
                notes.append([round(n.start, 3), round(n.end - n.start, 3), n.pitch, n.velocity])
            for c in piano.control_changes:
                made.append((tick(c.time), CONTROL + (c.number << 8) + c.value,
                             mido.Message("control_change", control=c.number, value=c.value)))
        with self._cond:  # what is played already all comes before what a part brings: only the rest moves
            self._notes.extend(notes)
            self._messages.extend(made)
            self._messages.sort(key=lambda m: m[:2])
            self.until = until
            last = math.inf if until == math.inf else tick(until)  # a part to come may bring this tick's own
            self._ready = bisect.bisect_left(self._messages, last, key=lambda m: m[0])
            self._cond.notify_all()
        self._changed()

    def fail(self) -> None:
        with self._cond:
            self.failed = True
            self._cond.notify_all()
        self._changed()

    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    @property
    def whole(self) -> bool:
        return self.until == math.inf

    def first_note(self) -> float | None:
        """When its first note sounds, as Player reads it from a file; None while it is not there yet."""
        at = 0.0
        for msg in self._timed(self._released()):
            at += msg.time
            if msg.type == "note_on" and msg.velocity:
                return at
        return None

    def plays(self) -> bool:
        """Whether it is ready to be played: PLAY_AHEAD_S of it there after its first note, or all of it."""
        first = self.first_note()
        return self.whole or first is not None and self.until >= first + PLAY_AHEAD_S

    def notes(self) -> list[list]:
        """Its notes there so far: [start, length, pitch, velocity], in seconds, by their start."""
        with self._cond:
            return sorted(self._notes)

    def messages(self, stopped: threading.Event):
        """Its messages, as mido gives a file's (time: seconds since the one before), as they come; None each time
        before it waits for the next. Ends with the piece, or when stopped is set; NotMade once it failed."""
        given, tick = 0, 0
        while not stopped.is_set():
            with self._cond:
                coming = self._messages[given:self._ready]
                failed, whole = self.failed, self.whole
            if coming:
                for msg in self._timed(coming, tick):
                    yield msg
                given, tick = given + len(coming), coming[-1][0]
                continue
            if failed:
                raise NotMade()
            if whole:
                return
            yield None
            with self._cond:
                self._cond.wait_for(lambda: self._ready > given or self.failed, timeout=0.1)

    def _released(self) -> list:
        with self._cond:
            return self._messages[:self._ready]

    @staticmethod
    def _timed(messages, tick: int = 0):
        tempo = mido.bpm2tempo(TEMPO)
        for at, _, msg in messages:
            yield msg.copy(time=mido.tick2second(at - tick, RESOLUTION, tempo) if at > tick else 0)
            tick = at

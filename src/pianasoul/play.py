"""Play library MIDI files on a MIDI output: a piano over USB or Bluetooth, an app such as Pianoteq, or an iPad over a
network MIDI session.

    ./run.sh play someone like you        # best match in the library
    ./run.sh play path/to/file.mid
    ./run.sh play --all                   # every piece in the library, in order
    ./run.sh play --ports                 # list MIDI outputs

The outputs are CoreMIDI's destinations (macOS), each known by its unique ID; a name is only shown. The app plays on
the one chosen in it (Playback); otherwise the output is the first whose name contains --port, PIANASOUL_PORT or
"pianasoul". Before playing, and about once a second while playing, the player makes sure it is connected: online,
and for a network session joined by a device (the iPad).

Each message leaves a little early (the lead) together with the time it should sound. A network MIDI session carries
that time along, so the receiver plays every note on schedule even when Wi-Fi delivers it late: the lead is 1 second
there, and none for a piano on USB or Bluetooth (PIANASOUL_LEAD sets it for all). With a lead, nothing is sent
without a time. Pause, stop and a new volume take effect after the lead.

A piece being made (growing.Growing) plays as it comes: its music waits where its notes are not there yet, and goes
on from there.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import functools
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import show_details
from .growing import NotMade
from .library import Library

log = logging.getLogger(__spec__.name)

DEFAULT_PORT = "pianasoul"
NETWORK_LEAD_S = 1.0  # how early each message leaves for a network session
GAP_S = 2.0  # silence between pieces
LEAD_IN_S = 0.5  # a piece played from its start gives its first note this long: a moment, not at once (begin)
LOOK_EVERY_S = 1.0  # while playing, how often the player makes sure the piano is still there
HEARD_LATE_S = 0.2  # notes leaving this much later than they should sound are heard late: said in the log
SENT = {"note_on", "note_off", "control_change"}  # no program changes: they could switch the Pianoteq preset
VOLUME_CONTROL = 7  # MIDI's volume controller: the listener sets it (Player.volume), a file's own is not sent
ALL_NOTES_OFF = 123
COREMIDI = "/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI"
COREFOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
DESTINATION = 3  # kMIDIObjectType_Destination
KINDS = {"com.apple.AppleMIDIUSBDriver": "usb", "com.apple.AppleMIDIBluetoothDriver": "bluetooth",
         "com.apple.AppleMIDIRTPDriver": "network", "com.apple.AppleMIDIIACDriver": "virtual"}  # by driver
_client_lock = threading.Lock()
_client: int | None = None
_changes: list = []  # called, with nothing, whenever CoreMIDI's outputs change (on CoreMIDI's own thread)


@dataclass(frozen=True)
class Output:
    """A MIDI output. kind only says how it is reached, for its icon (usb, bluetooth, network, virtual or other);
    peers are the devices that have joined a network session, None for any other output."""
    id: int  # CoreMIDI's unique ID: the output, whatever its name
    name: str
    kind: str
    online: bool
    peers: tuple[str, ...] | None
    endpoint: int = 0

    @property
    def connected(self) -> bool:
        return self.online and self.peers != ()

    def public(self) -> dict:
        found = {"id": self.id, "name": self.name, "kind": self.kind, "connected": self.connected}
        return found | ({"peers": list(self.peers)} if self.peers is not None else {})


def outputs() -> list[Output]:
    """Every MIDI output this Mac knows, connected or not: its devices' (a piano switched off stays listed) and those
    apps make (Pianoteq, say)."""
    if sys.platform != "darwin":
        return []
    client()
    cm, found, seen = _coremidi(), [], set()
    for d in range(cm.MIDIGetNumberOfDevices()):
        device = cm.MIDIGetDevice(d)
        for e in range(cm.MIDIDeviceGetNumberOfEntities(device)):
            entity = cm.MIDIDeviceGetEntity(device, e)
            for i in range(cm.MIDIEntityGetNumberOfDestinations(entity)):
                endpoint = cm.MIDIEntityGetDestination(entity, i)
                found.append(_output(endpoint, entity, device))
                seen.add(endpoint)
    for i in range(cm.MIDIGetNumberOfDestinations()):
        endpoint = cm.MIDIGetDestination(i)
        if endpoint not in seen:  # an app's own, with no device
            found.append(_output(endpoint, 0, 0))
    return found


def output(output_id: int) -> Output | None:
    """One output, as it is now; None when CoreMIDI has no such output."""
    if sys.platform != "darwin":
        return None
    from ctypes import byref, c_int32, c_uint32

    client()
    cm, endpoint, kind = _coremidi(), c_uint32(), c_int32()
    if cm.MIDIObjectFindByUniqueID(output_id, byref(endpoint), byref(kind)) or kind.value != DESTINATION:
        return None
    entity, device = c_uint32(), c_uint32()
    if cm.MIDIEndpointGetEntity(endpoint, byref(entity)) == 0 and entity.value:
        cm.MIDIEntityGetDevice(entity, byref(device))
    return _output(endpoint.value, entity.value, device.value)


def by_name(wanted: str) -> Output | None:
    """The first output whose name contains wanted (any case), a connected one before one that is not."""
    found = [o for o in outputs() if wanted.casefold() in o.name.casefold()]
    return max(found, key=lambda o: o.connected, default=None)


def _output(endpoint: int, entity: int, device: int) -> Output:
    peers = session_peers(entity) if entity else None
    owner = _string(device, "kMIDIPropertyDriverOwner") if device else ""
    kind = "network" if peers is not None else KINDS.get(owner, "other" if device else "virtual")
    offline = any(_integer(o, "kMIDIPropertyOffline") for o in (endpoint, entity, device) if o)
    name = _string(endpoint, "kMIDIPropertyDisplayName") or _string(endpoint, "kMIDIPropertyName")
    return Output(_integer(endpoint, "kMIDIPropertyUniqueID"), name, kind, not offline, peers, endpoint)


def session_peers(entity: int) -> tuple[str, ...] | None:
    """The devices that have joined the network MIDI session that is this entity (its Participants in MIDI Network
    Setup), by name; "" for one the Mac does not name. None when it is not a network session, or the Mac does not
    say.

    macOS lists them as "peers" in the session's "apple.midirtp.session" property; read only."""
    from ctypes import byref, c_void_p

    try:
        cm, cf = _coremidi(), _corefoundation()
        session = c_void_p()
        if cm.MIDIObjectGetDictionaryProperty(entity, _cfstring("apple.midirtp.session"), byref(session)):
            return None  # not a network session
        try:
            peers = cf.CFDictionaryGetValue(session, _cfstring("peers"))
            if not peers or cf.CFGetTypeID(peers) != cf.CFArrayGetTypeID():
                return None  # not laid out as expected: no claim either way
            return tuple(_peer_name(cf.CFArrayGetValueAtIndex(peers, i)) for i in range(cf.CFArrayGetCount(peers)))
        finally:
            cf.CFRelease(session)
    except Exception:
        return None


def _peer_name(peer) -> str:
    """A participant's name, from its entry in the session's "peers"."""
    cf = _corefoundation()
    if not peer or cf.CFGetTypeID(peer) != cf.CFDictionaryGetTypeID():
        return ""
    name = cf.CFDictionaryGetValue(peer, _cfstring("name"))
    return _text(name) if name and cf.CFGetTypeID(name) == cf.CFStringGetTypeID() else ""


def _integer(obj: int, key: str) -> int:
    value = ctypes.c_int32()
    return value.value if _coremidi().MIDIObjectGetIntegerProperty(obj, _property(key), ctypes.byref(value)) == 0 else 0


def _string(obj: int, key: str) -> str:
    value = ctypes.c_void_p()
    if _coremidi().MIDIObjectGetStringProperty(obj, _property(key), ctypes.byref(value)) or not value.value:
        return ""
    try:
        return _text(value)
    finally:
        _corefoundation().CFRelease(value)


def _text(cfstring) -> str:
    buffer = ctypes.create_string_buffer(1024)
    return buffer.value.decode() if _corefoundation().CFStringGetCString(cfstring, buffer, len(buffer), 0x08000100) \
        else ""


@functools.cache
def _property(key: str) -> int:
    """A CoreMIDI property's key, as CoreMIDI defines it (kMIDIPropertyUniqueID and the like)."""
    return ctypes.c_void_p.in_dll(_coremidi(), key).value


def on_change(listener) -> None:
    """listener() is called whenever CoreMIDI's outputs change: one comes or goes, goes online or offline, or a
    property of one changes. Called on CoreMIDI's thread; a listener only takes note. macOS only."""
    _changes.append(listener)
    if sys.platform == "darwin":
        client()


@ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
def _notified(message, _ref) -> None:
    for listener in list(_changes):
        try:
            listener()
        except Exception:
            log.exception("a listener to CoreMIDI's changes failed")


def client() -> int:
    """The program's CoreMIDI client, made once on a thread of its own that runs a run loop: CoreMIDI passes the
    changes in its outputs to that run loop, so that every thread sees them as they are, and tells on_change's
    listeners."""
    global _client
    with _client_lock:
        if _client is None:
            ready, made = threading.Event(), ctypes.c_uint32()

            def run():
                try:
                    if _coremidi().MIDIClientCreate(_cfstring("PianaSoul"), ctypes.cast(_notified, ctypes.c_void_p),
                                                    None, ctypes.byref(made)):
                        made.value = 0
                finally:
                    ready.set()
                _corefoundation().CFRunLoopRun()
            threading.Thread(target=run, daemon=True).start()
            ready.wait(5)
            if not made.value:
                raise OSError("CoreMIDI did not start")
            _client = made.value
        return _client


@functools.cache
def _coremidi():
    """CoreMIDI, with the calls used here typed. macOS only."""
    from ctypes import POINTER, c_int32, c_uint32, c_uint64, c_ulong, c_void_p

    cm = ctypes.CDLL(COREMIDI)
    cm.MIDIClientCreate.argtypes = [c_void_p, c_void_p, c_void_p, POINTER(c_uint32)]
    cm.MIDIClientCreate.restype = c_int32
    cm.MIDIOutputPortCreate.argtypes = [c_uint32, c_void_p, POINTER(c_uint32)]
    cm.MIDIOutputPortCreate.restype = c_int32
    cm.MIDIGetNumberOfDevices.restype = cm.MIDIGetNumberOfDestinations.restype = c_ulong
    cm.MIDIGetDevice.argtypes = cm.MIDIGetDestination.argtypes = [c_ulong]
    cm.MIDIGetDevice.restype = cm.MIDIGetDestination.restype = c_uint32
    cm.MIDIDeviceGetNumberOfEntities.argtypes = cm.MIDIEntityGetNumberOfDestinations.argtypes = [c_uint32]
    cm.MIDIDeviceGetNumberOfEntities.restype = cm.MIDIEntityGetNumberOfDestinations.restype = c_ulong
    cm.MIDIDeviceGetEntity.argtypes = cm.MIDIEntityGetDestination.argtypes = [c_uint32, c_ulong]
    cm.MIDIDeviceGetEntity.restype = cm.MIDIEntityGetDestination.restype = c_uint32
    cm.MIDIEndpointGetEntity.argtypes = cm.MIDIEntityGetDevice.argtypes = [c_uint32, POINTER(c_uint32)]
    cm.MIDIEndpointGetEntity.restype = cm.MIDIEntityGetDevice.restype = c_int32
    cm.MIDIObjectFindByUniqueID.argtypes = [c_int32, POINTER(c_uint32), POINTER(c_int32)]
    cm.MIDIObjectFindByUniqueID.restype = c_int32
    cm.MIDIObjectGetIntegerProperty.argtypes = [c_uint32, c_void_p, POINTER(c_int32)]
    cm.MIDIObjectGetIntegerProperty.restype = c_int32
    cm.MIDIObjectGetStringProperty.argtypes = [c_uint32, c_void_p, POINTER(c_void_p)]
    cm.MIDIObjectGetStringProperty.restype = c_int32
    cm.MIDIObjectGetDictionaryProperty.argtypes = [c_uint32, c_void_p, POINTER(c_void_p)]
    cm.MIDIObjectGetDictionaryProperty.restype = c_int32
    cm.MIDIPacketListInit.argtypes = [c_void_p]
    cm.MIDIPacketListInit.restype = c_void_p
    cm.MIDIPacketListAdd.argtypes = [c_void_p, c_ulong, c_void_p, c_uint64, c_ulong, c_void_p]
    cm.MIDIPacketListAdd.restype = c_void_p
    cm.MIDISend.argtypes = [c_uint32, c_uint32, c_void_p]
    cm.MIDISend.restype = c_int32
    cm.MIDIPortDispose.argtypes = [c_uint32]
    return cm


@functools.cache
def _corefoundation():
    """CoreFoundation, with the calls used here typed. macOS only."""
    from ctypes import c_char_p, c_long, c_uint32, c_ulong, c_void_p

    cf = ctypes.CDLL(COREFOUNDATION)
    cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
    cf.CFStringCreateWithCString.restype = c_void_p
    cf.CFDictionaryGetValue.argtypes = [c_void_p, c_void_p]
    cf.CFDictionaryGetValue.restype = c_void_p
    cf.CFGetTypeID.argtypes = [c_void_p]
    cf.CFGetTypeID.restype = cf.CFArrayGetTypeID.restype = c_ulong
    cf.CFArrayGetCount.argtypes = [c_void_p]
    cf.CFArrayGetCount.restype = c_long
    cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
    cf.CFArrayGetValueAtIndex.restype = c_void_p
    cf.CFDictionaryGetTypeID.restype = cf.CFStringGetTypeID.restype = c_ulong
    cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [c_void_p]
    return cf


@functools.cache
def _cfstring(text: str) -> int:
    """A CoreFoundation string, made once and kept."""
    return _corefoundation().CFStringCreateWithCString(None, text.encode(), 0x08000100)  # UTF-8


def arpeggio():
    """The test tone: C, E, G and the C above, a little apart, then held together; about 1.2 seconds."""
    import mido

    notes = (60, 64, 67, 72)
    events = [(i * 0.15, mido.Message("note_on", note=n, velocity=80)) for i, n in enumerate(notes)]
    events += [(1.2, mido.Message("note_off", note=n)) for n in notes]
    midi = mido.MidiFile(ticks_per_beat=480)  # at the default 120 beats a minute: 960 ticks a second
    track = mido.MidiTrack()
    midi.tracks.append(track)
    previous = 0.0
    for at, msg in events:
        track.append(msg.copy(time=round((at - previous) * 960)))
        previous = at
    return midi


def default_lead(out: Output) -> float:
    """How early each message leaves: PIANASOUL_LEAD, or a second for a network session and none otherwise."""
    text = os.environ.get("PIANASOUL_LEAD", "").strip()
    try:
        return max(0.0, float(text)) if text else NETWORK_LEAD_S if out.peers is not None else 0.0
    except ValueError:
        return NETWORK_LEAD_S if out.peers is not None else 0.0


@functools.lru_cache(maxsize=256)
def _first_note(path: str, changed: int) -> float:
    import mido

    at = 0.0
    for msg in mido.MidiFile(path):
        at += msg.time
        if msg.type == "note_on" and msg.velocity:
            return at
    return 0.0


def begin(path: Path, from_s: float, lead: float = 0.0, first: float | None = None) -> float:
    """Where a piece starts: at from_s; played from its start (0, or below: after that much silence), so that its
    first note is heard LEAD_IN_S after it starts: the silence a file may open with (a video's opening, say) is not
    waited through, and the first note does not come at once either. Notes sent a lead early (a network session) are
    heard that long after anyway, which counts. first: when its first note sounds, if it is known otherwise (a piece
    being made: Player.begin); else it is read from the file."""
    if from_s > 0:
        return from_s
    if first is None:
        try:
            first = _first_note(str(path), Path(path).stat().st_mtime_ns)
        except Exception:  # a file that cannot be read fails where it is played, as it is
            return from_s
    return from_s + max(0.0, first - max(0.0, LEAD_IN_S - lead))


def clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def unavailable(out: Output) -> str:
    """Why an output that is there cannot be played on now, or empty when it can."""
    if not out.online:
        return f'"{out.name}" is not connected'
    if out.peers == ():
        return (f'the network MIDI session "{out.name}" has no participants. Open Pianoteq on the iPad and connect it '
                f"in MIDI Network Setup.")
    return ""


class CoreMidiOut:
    """A macOS MIDI output that stamps every message with the time it should sound (or sends it at once).

    mido cannot pass these times on, so this calls CoreMIDI directly. Times are perf_counter() values.
    """

    def __init__(self, out: Output):
        from ctypes import byref, c_uint32, c_uint64

        cm = _coremidi()
        system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        system.mach_absolute_time.restype = c_uint64

        class Timebase(ctypes.Structure):
            _fields_ = [("numer", c_uint32), ("denom", c_uint32)]

        timebase = Timebase()
        system.mach_timebase_info(byref(timebase))
        self._ticks_per_s = 1e9 * timebase.denom / timebase.numer
        self._cm, self._host_time, self._ubyte = cm, system.mach_absolute_time, ctypes.c_ubyte
        self._dest, self._port = out.endpoint, c_uint32()
        if cm.MIDIOutputPortCreate(client(), _cfstring("PianaSoul out"), byref(self._port)) != 0:
            raise OSError("CoreMIDI did not open an output")
        self._buffer = ctypes.create_string_buffer(1024)
        self._lock = threading.Lock()

    def send(self, msg, when: float | None = None) -> None:
        """Send a mido message to sound at perf_counter() time when; at once if when is None or past."""
        data = msg.bytes()
        with self._lock:
            stamp = 0  # zero means now
            if when is not None:
                stamp = max(0, self._host_time() + round((when - time.perf_counter()) * self._ticks_per_s))
            packet = self._cm.MIDIPacketListInit(self._buffer)
            packet = self._cm.MIDIPacketListAdd(self._buffer, len(self._buffer), packet, stamp, len(data),
                                                (self._ubyte * len(data))(*data))
            if not packet or self._cm.MIDISend(self._port, self._dest, self._buffer) != 0:
                raise OSError("CoreMIDI could not send")

    def close(self) -> None:
        self._cm.MIDIPortDispose(self._port)


class Player:
    """Sends MIDI files to one output, in the foreground (play) or in a background thread (start).

    Like a media player: pause keeps the position (start from it to go on), stop goes back to the beginning.
    With a lead, every message leaves that many seconds early, stamped with the time it should sound, and
    pause or stop lets the notes already sent finish first.
    """

    def __init__(self, port_name: str | None = None, port=None, lead: float | None = None):
        # Without a port, the player opens the output itself each time playback starts (see check_connection): the
        # one target() names, with the lead asked for, or its own (default_lead).
        self.opens = port is None
        self.wanted = port_name or os.environ.get("PIANASOUL_PORT") or DEFAULT_PORT
        self.lead_asked = None if lead is None else max(0.0, lead)
        self.port, self.port_name = port, port_name or "test"
        self.output: Output | None = None  # the output open now
        self.lead = lead or 0.0  # seconds each message leaves before it should sound; 0 sends it when due
        self.current: tuple[Path, str] | None = None  # the piece playing, paused or last played
        self.state = "stopped"  # playing, paused or stopped
        self._offset = 0.0  # seconds into the current piece while paused
        self._t0: float | None = None  # perf_counter() when 0:00 of the current piece sounds
        self._from = 0.0  # where the current run started, in seconds into the piece
        self._sent_to = 0.0  # seconds into the piece up to which every message has been sent
        self._last_when = 0.0  # the latest perf_counter() time a note or the pedal was stamped for: the music's end
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._down: set[tuple[int, int]] = set()
        self.completed: tuple[str, float] | None = None  # natural finish: title and audible end time
        self.lost: str | None = None  # why the piano went away while playing; playback paused there
        self.volume: int | None = None  # percent of the piano's full volume; None leaves the piano's own
        self.growing: dict = {}  # the pieces being made that can play, by the file each will have (growing.Growing)
        self._making = None  # the one of them playing now

    @property
    def position(self) -> float:
        """Seconds into the current piece, as heard (0 during the silence before it); a piece being made, no further
        than it is there, but where it was taken to (a seek past that waits there)."""
        if self.state == "playing" and self._t0 is not None:
            heard = time.perf_counter() - self._t0
            return max(0.0, self._from, min(heard, self._making.until) if self._making else heard)
        return self._offset

    def playable(self, path: Path) -> bool:
        """Whether a piece is there to play: its file, or it is being made and can play (growing)."""
        return Path(path).is_file() or Path(path) in self.growing

    def begin(self, path: Path, from_s: float) -> float:
        """Where a piece starts (begin), its first note known from what is there of it while it is being made."""
        piece = self.growing.get(Path(path))
        return begin(path, from_s, self.lead, piece.first_note() if piece else None)

    def play(self, items: list[tuple[Path, str]], from_s: float = 0.0, on_start=None) -> None:
        """Play (path, title) items in order, the first from from_s (below 0: after that much silence), the others
        from their start (begin); returns when everything is sent, or paused or stopped. With a lead, the music goes
        on for that long after this returns. after_run() follows, once the keys and the pedal are up."""
        try:
            for i, (path, title) in enumerate(items):
                if i and self._stop.wait(GAP_S):
                    return
                if self._stop.is_set():
                    return
                self.current, self.state = (path, title), "playing"
                if on_start:
                    on_start(path, title)
                if not self._play_file(path, self.begin(path, from_s if i == 0 else 0.0)):
                    return  # pause() or stop() sets the state, or the piano went away (lost)
            self.state, self._offset = "stopped", 0.0
            if self.current and not self._stop.is_set():
                self.completed = (self.current[1], max(time.perf_counter(), self._last_when))
        finally:
            self.release()
            self.after_run()

    def after_run(self) -> None:
        """Called on the playing thread when it is done: the piece finished (completed), the piano went away
        (lost), or pause or stop ended it."""

    def waited(self) -> None:
        """Called on the playing thread when a piece being made goes on after its music waited for its notes: from
        now on it is heard that much later."""

    def target(self) -> Output | None:
        """The output to play on, connected or not, or None when there is none: here the first named like wanted.
        Playback plays on the one chosen in the app."""
        return by_name(self.wanted)

    def missing(self) -> str:
        """Why there is no output to play on."""
        names = ", ".join(o.name for o in outputs() if o.connected) or "none"
        return f'no MIDI output matching "{self.wanted}" (available: {names})'

    def find_piano(self) -> Output:
        """The output to play on, connected; LookupError, with why, when there is none, or it is not connected:
        offline, or a network session no device has joined (macOS keeps the session's output while the iPad is
        away: Pianoteq closed, or the iPad offline)."""
        out = self.target()
        if out is None:
            raise LookupError(self.missing())
        if why := unavailable(out):
            raise LookupError(why)
        return out

    def look_for_piano(self) -> None:
        """LookupError when the piano is not there. Only looks: start() opens it."""
        if self.opens:
            self.find_piano()

    def check_connection(self) -> None:
        """LookupError when the piano is not there. Before notes go out it opens the output afresh, so a piano
        that disconnected and came back plays again; while notes are on their way on the output open, it only looks
        for it."""
        self.lost = None  # an earlier loss is old news once playback is tried again
        if self.opens:
            if self.port is not None and (self.state == "playing" or time.perf_counter() < self._last_when):
                self.find_piano()
            else:
                self._open()

    def _still_there(self) -> None:
        """While notes go out: LookupError when the output open now is gone or no longer connected."""
        out = output(self.output.id) if self.output else None
        if out is None:
            raise LookupError(f'"{self.port_name}" is gone')
        if why := unavailable(out):
            raise LookupError(why)

    def _open(self) -> None:
        """Open the output, closing the one before. A piano that comes back is a new output."""
        out = self.find_piano()
        lead = default_lead(out) if self.lead_asked is None else self.lead_asked
        port = CoreMidiOut(out)
        self._close_port()
        self.port, self.output, self.port_name, self.lead = port, out, out.name, lead
        log.debug("MIDI output %s (%s), notes %s", out.name, out.kind,
                  f"sent {lead:g} s ahead" if lead else "sent when due")

    def switch(self) -> None:
        """Another output from now on: a piece playing pauses where it is, every key and the pedal come up on the old
        one, and the next notes go to the new one. A test still sounding stops first."""
        self.pause()
        self._halt()
        self._close_port()

    def _close_port(self) -> None:
        """Close the output open now, once every note on it is off (All Notes Off, after the notes already sent)."""
        if self.port is None or not self.opens:
            return
        import mido

        when = max(self._last_when, time.perf_counter() + self.lead) if self.lead else None
        with contextlib.suppress(OSError):  # a piano that went away has nothing to lift
            for channel in range(16):
                self._send(mido.Message("control_change", channel=channel, control=ALL_NOTES_OFF, value=0), when,
                           setting=True)
        self.port.close()
        self.port, self.output = None, None

    def test_tone(self) -> None:
        """A short C major arpeggio on the output, at the volume set: to hear that the piano is there. Sent the way a
        piece is (a very short one), from a thread of its own; a piece played meanwhile takes over. LookupError when
        the piano is not there; RuntimeError while a piece is playing."""
        if self.state == "playing":
            raise RuntimeError("a piece is playing")
        self.check_connection()
        self._halt()  # a test still sounding gives way
        self._stop.clear()
        self._thread = threading.Thread(target=self._sound_test, daemon=True)
        self._thread.start()

    def _sound_test(self) -> None:
        try:
            self._send_midi(arpeggio())
        except (LookupError, OSError) as exc:  # the piano went just now: the next try says so
            log.debug("the test tone stopped: %s", exc)
        finally:
            self.release()

    def start(self, items: list[tuple[Path, str]], from_s: float = 0.0, on_ready=None) -> None:
        """Play items in a thread of their own. FileNotFoundError when the piece is not there to play (playable),
        LookupError when the piano is not there: then nothing changes."""
        if not self.playable(items[0][0]):
            raise FileNotFoundError(items[0][0])
        self.check_connection()
        self._halt()
        if on_ready:
            on_ready()
        self._launch(items, from_s)

    def _launch(self, items: list[tuple[Path, str]], from_s: float) -> None:
        self._stop.clear()  # here, not in the thread, so an immediate pause() cannot be lost
        start = self.begin(items[0][0], from_s)  # as the thread will find it (play): the position meanwhile
        self.current, self.state, self._offset = items[0], "playing", start
        log.debug("plays %s from %s%s", items[0][1], clock(max(0.0, start)),
                  "" if self.volume is None else f" at volume {self.volume}%")
        self._t0, self._from, self._sent_to = time.perf_counter() + self.lead - start, start, start
        self._thread = threading.Thread(target=self.play, args=(items, from_s), daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Pause where the notes already sent run out (about one lead from now)."""
        if self.state == "playing":
            self._halt()
            self.state, self._offset = "paused", max(0.0, self._sent_to)
            log.debug("paused at %s", clock(self._offset))

    def stop(self) -> None:
        self._halt()
        self.state, self._offset = "stopped", 0.0

    def set_volume(self, percent: float) -> None:
        """The piano's volume, in percent of its full range. Heard at once, after the lead like everything sent (the
        output is opened for it if none is open yet), and told again before each piece, so a piano that came back or
        started afresh has it too."""
        self.volume = max(0, min(100, round(percent)))
        try:
            if self.port is None and self.opens:
                self._open()
            if self.port is not None:
                self._send_volume()
                log.debug("volume %d%%, heard at once", self.volume)
                return
        except (LookupError, OSError):
            pass
        log.debug("volume %d%%, for the next piece: the piano is not there now", self.volume)

    def _send_volume(self, when: float | None = None) -> None:
        """The volume on every channel, as the pedal is lifted on every channel: a file may use any of them."""
        import mido

        value = round(self.volume * 127 / 100)
        for channel in range(16):
            self._send(mido.Message("control_change", channel=channel, control=VOLUME_CONTROL, value=value), when,
                       setting=True)

    def close(self) -> None:
        """Stop, let the notes already sent finish, and close the output, every note off. Call before the program
        exits."""
        self.stop()
        wait = min(self._last_when - time.perf_counter(), max(self.lead, 1.5))  # a test tone sounds 1.2 s
        if wait > 0:
            time.sleep(wait + 0.05)
        if self.opens:
            self._close_port()
        elif self.port is not None:
            self.port.close()

    def _halt(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        self.completed = None

    def _send(self, msg, when: float | None = None, setting: bool = False) -> None:
        """With a lead, every message carries a time, one lead from now unless given, never none: an iPad on a network
        session plays a note stamped for later at once when it arrives with a message stamped for now (M3, recorded).
        A setting (the volume, All Notes Off) is not music: it does not make the music last longer."""
        if self.lead:
            when = time.perf_counter() + self.lead if when is None else when
            self.port.send(msg, when)
            if not setting:
                self._last_when = max(self._last_when, when)
        else:
            self.port.send(msg)

    def _play_file(self, path: Path, from_s: float = 0.0) -> bool:
        """Send one file (or a piece being made, as it comes) from from_s seconds, each message one lead early. True
        when it reached the end; when the piano goes away, playback pauses where its sound stopped; a piece that is
        not made after all stops."""
        import mido

        self._making = self.growing.get(Path(path))
        try:
            return self._send_midi(self._making.messages(self._stop) if self._making else mido.MidiFile(path), from_s)
        except NotMade:
            self.state, self._offset = "stopped", 0.0
            log.debug("not made after all: %s", Path(path).name)
            return False
        except (LookupError, OSError) as exc:  # the piano went away (not found, or the notes no longer go out):
            heard = time.perf_counter() - self._t0 - LOOK_EVERY_S  # pause where the sound stopped, a moment back
            self.state, self._offset, self.lost = "paused", max(0.0, self._from, heard), str(exc)
            log.debug("the piano went away at %s: %s", clock(self._offset), exc)
            return False
        finally:
            self._making = None

    def _send_midi(self, midi, from_s: float = 0.0) -> bool:
        """Send a mido.MidiFile (or a piece being made, as it comes: None when its next message is not there yet)
        from from_s seconds, each message one lead early (with its time, which a network session carries along). True
        when it reached the end, False when stopped; LookupError or OSError when the piano goes away."""
        import mido

        held: dict[tuple[int, int], int] = {}  # each controller as it is at from_s (the pedals), sent before going on
        self._t0 = time.perf_counter() + self.lead - from_s
        self._from = self._sent_to = from_s
        at, restored = 0.0, from_s <= 0
        late: list[float] = []  # seconds each message left after its time, for the details
        stall: tuple[float, float] | None = None  # notes heard late now: where it began in the piece, and the latest
        look_at = time.perf_counter() + LOOK_EVERY_S
        waited = False  # a piece being made: its next message was not there when it was asked for
        try:
            if self.volume is not None:
                self._send_volume(self._t0 + from_s)  # first, with the time the first notes sound
            for msg in midi:  # merged tracks, msg.time in seconds since the previous message
                if msg is None:
                    waited = True
                    continue
                at += msg.time
                if msg.is_meta or msg.type not in SENT:
                    continue
                if msg.type == "control_change" and msg.control == VOLUME_CONTROL:
                    continue  # the listener's, not the file's
                if at < from_s:
                    if msg.type == "control_change" and msg.control < 120:  # not the channel mode messages
                        held[msg.channel, msg.control] = msg.value
                    continue
                if waited:  # the music waited for this message: it goes on from it now, as late as it came
                    waited, behind = False, time.perf_counter() - (self._t0 + at - self.lead)
                    if behind > 0:
                        self._t0 += behind
                        log.debug("waited %.1f s for its notes at %s", behind, clock(at))
                        self.waited()
                if not restored:
                    for (channel, control), value in held.items():
                        self._send(mido.Message("control_change", channel=channel, control=control, value=value),
                                   self._t0 + from_s)
                    restored = True
                due = self._t0 + at - self.lead  # when this message should leave
                if self._wait_until(due):
                    return False
                if self.opens and time.perf_counter() >= look_at:
                    look_at = time.perf_counter() + LOOK_EVERY_S
                    self._still_there()
                self._send(msg, self._t0 + at)
                self._sent_to = at
                late.append(time.perf_counter() - due)
                heard = late[-1] - self.lead  # what the lead does not make up for
                if heard > HEARD_LATE_S:
                    stall = (stall[0] if stall else at, max(heard, stall[1] if stall else 0.0))
                elif stall:
                    self._said_late(*stall)
                    stall = None
                if msg.type == "note_on" and msg.velocity > 0:
                    self._down.add((msg.channel, msg.note))
                elif msg.type in {"note_on", "note_off"}:
                    self._down.discard((msg.channel, msg.note))
            return not self._stop.is_set()
        finally:  # everything due to leave by now has left, so the music runs up to here (what is there of it)
            if stall:
                self._said_late(*stall)
            ran = time.perf_counter() - self._t0 + self.lead
            self._sent_to = max(self._sent_to, min(ran, self._making.until) if self._making else ran)
            if late and log.isEnabledFor(logging.DEBUG):
                late = sorted(x * 1000 for x in late)
                log.debug("timing: %d messages left a median %.1f ms late, 99%% within %.1f ms, the latest %.1f ms%s",
                          len(late), late[len(late) // 2], late[int(len(late) * 0.99)], late[-1],
                          f"; sent {self.lead:g} s early, only a delay beyond that would be heard" if self.lead
                          else "")

    def _said_late(self, at: float, heard: float) -> None:
        """Notes that left so late they are heard late, said once each time it happens: whether a piece that stops
        and then hurries on was this program's doing (the notes left late) or the way to the piano's (they left on
        time, and nothing is said)."""
        kind = f" ({self.output.kind})" if self.output else ""
        print(f"  (notes left up to {heard:.1f} s late at {clock(at)}, on {self.port_name}{kind})", flush=True)

    def _wait_until(self, when: float) -> bool:
        """Sleep until perf_counter() reaches when. True if stopped meanwhile."""
        delay = when - time.perf_counter()
        return (self._stop.wait(delay) if delay > 0 else False) or self._stop.is_set()

    def release(self) -> None:
        """Lift every key that is still down and the sustain pedal, timed for where the music stops."""
        import mido

        when = self._t0 + self._sent_to if self.lead and self._t0 is not None else None
        up = [mido.Message("note_off", channel=channel, note=note, velocity=0) for channel, note in sorted(self._down)]
        up += [mido.Message("control_change", channel=channel, control=64, value=0) for channel in range(16)]
        self._down.clear()
        for msg in up:
            with contextlib.suppress(OSError):  # a piano that went away has nothing to lift
                self._send(msg, when)


def resolve(lib: Library, words: list[str]) -> list[tuple[Path, str]]:
    """A MIDI file path, or the best library match for the words."""
    text = " ".join(words).strip()
    path = Path(text).expanduser()
    if path.suffix.lower() in {".mid", ".midi"} and path.is_file():
        return [(path, path.stem)]
    for rec in lib.search(text):
        if rec["file_exists"]:
            return [(Path(rec["midi_path"]), rec["title"])]
    return []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pianasoul play", description="Play library MIDI files on a MIDI output.")
    p.add_argument("query", nargs="*", help="words to find in the library, or a MIDI file path")
    p.add_argument("--all", action="store_true", help="play every piece in the library, in order")
    p.add_argument("--port", help=f'MIDI output name or part of it (default: PIANASOUL_PORT or "{DEFAULT_PORT}")')
    p.add_argument("--ports", action="store_true", help="list MIDI outputs and exit")
    p.add_argument("--lead", type=float, metavar="SECONDS",
                   help=f"send each note this early with its time (default: PIANASOUL_LEAD, or {NETWORK_LEAD_S:g} for "
                        "a network session and 0 otherwise)")
    p.add_argument("--debug", action="store_true", help="also show what happens in detail, with times (how "
                   "punctually each piece was sent, too)")
    args = p.parse_args(argv)
    show_details(args.debug)

    if args.ports:
        found = outputs()
        for out in found:
            joined = ", ".join(p or "unnamed" for p in out.peers or []) or "none"
            print(f"{out.name}  ({out.kind}, {'connected' if out.connected else 'not connected'}"
                  f"{f', participants: {joined}' if out.peers is not None else ''}; id {out.id})")
        if not found:
            print("No MIDI outputs found.")
        return 0
    lib = Library()
    if args.all:
        items = [(Path(r["midi_path"]), r["title"]) for r in lib.playable()]
    elif args.query:
        items = resolve(lib, args.query)
    else:
        p.error("give words to search the library, a MIDI file, or --all")
    if not items:
        print("Nothing found in the library. Try ./run.sh library list", file=sys.stderr)
        return 1
    try:
        player = Player(args.port, lead=args.lead)
        player.check_connection()
    except LookupError as exc:
        print(f"Cannot play: {exc}", file=sys.stderr)
        return 2

    def announce(path: Path, title: str) -> None:
        import mido

        print(f"▶ {title} ({clock(mido.MidiFile(path).length)})", flush=True)

    how = f"notes sent {player.lead:g} s ahead" if player.lead else "notes sent when due"
    print(f"Playing on {player.port_name} ({how}). Ctrl+C to stop.")
    try:
        player.play(items, on_start=announce)
    except KeyboardInterrupt:  # play() has already timed the release of the keys and the pedal
        print("\nStopped.")
    if player.lost:
        print(f"Stopped at {clock(player.position)}: {player.lost}", file=sys.stderr)
    try:
        player.close()  # the notes already sent still play
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

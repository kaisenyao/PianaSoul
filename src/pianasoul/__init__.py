"""PianaSoul: piano recordings made into performance MIDI, and an assistant who plays them on your piano."""

import logging

__version__ = "0.1.0"


def show_details(on: bool) -> None:
    """--debug, in chat, voice, the app, play and transcription: what PianaSoul does in detail, each line with its
    time, on the terminal as it happens. The details are logged as they always are; this only shows them. Each module
    logs under its own name, logging.getLogger(__spec__.name): __name__ would be __main__ for the module a command runs
    (python -m)."""
    if not on:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("    %(asctime)s.%(msecs)03d %(module)-9s %(message)s", "%H:%M:%S"))
    log = logging.getLogger(__name__)
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    log.propagate = False  # PianaSoul's own lines only, not those of the libraries it uses

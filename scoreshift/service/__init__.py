"""HTTP front end for the ScoreShift engine.

The Windows app calls :mod:`scoreshift.engine` in process.  This package puts
the identical engine behind a small JSON API so a phone - an iOS client, say -
can send photographs and get back a transposed PDF and MIDI without needing
Java, OpenCV or music21 on the device.
"""
from .app import create_app  # noqa: F401

"""Blender model: a car alternator (D-121, D-123).

The 3D model itself is `bpy/alternator.py` (it only runs inside Blender, on the render PC). This
file is what the factory needs on the laptop: the verified facts the narration may use, and the
film's scenes in order, so the script follows what the film shows.
"""

from __future__ import annotations

from factory.blueprint.canvas import Fact

MACHINE = "alternator"
MODEL_FILE = "alternator.py"  # in factory/blender/bpy/

AUTO = "https://en.wikipedia.org/wiki/Alternator_(automotive)"
ALTERNATOR = "https://en.wikipedia.org/wiki/Alternator"
MINDSET = "https://theengineeringmindset.com/car-alternator-how-alternators-work/"
MVW = "https://www.mvwautomotive.com/alternator/"
INTECH = "https://www.intechopen.com/chapters/38166"

# Every fact has two independent pages that agree (D-122); checked 2026-11-07.
FACTS: dict[str, Fact] = {
    "phases": Fact(
        "3 phases",
        ALTERNATOR,
        about="stator windings: three sets, three-phase AC",
        second_source=MINDSET,
    ),
    "diodes": Fact(
        "6 diodes", MINDSET, about="rectifier: a full three-phase bridge", second_source=MVW
    ),
    "slip_rings": Fact(
        "2 slip rings",
        ALTERNATOR,
        about="rotor: the field current's two contacts",
        second_source=MINDSET,
    ),
    # Wikipedia says 2-3 times, IntechOpen about 2 times: the film and script use the range.
    "speed": Fact(
        "2 to 3 times",
        AUTO,
        about="alternator speed compared with the crankshaft",
        second_source=INTECH,
    ),
}

# The film's scenes, in order (roles as in blueprint films: hook, body..., note, close). Each line
# says what the narration explains while that scene plays, never what the picture looks like, so
# Gemini can't copy picture descriptions into the narration.
ROLES = ("hook", "body", "body", "body", "note", "close")
OUTLINE = (
    "hook: the whole alternator",
    "body part 1: the rotor: the engine's belt turns it, and its field coil, fed by a small direct "
    "current, makes it a spinning electromagnet",
    "body part 2: the stator: the moving magnetic field induces alternating current in its 3 sets "
    "of copper windings (three-phase)",
    "body part 3: the rectifier: 6 diodes turn that alternating current into the direct current "
    "the battery and the car's electronics need",
    "Engineer's Note: the opened alternator as a whole (the note may be about any part)",
    "close: the whole alternator again",
)

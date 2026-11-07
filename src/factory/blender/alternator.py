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

# The film's scenes, in order (roles as in blueprint films: hook, body..., note, close).
ROLES = ("hook", "body", "body", "body", "note", "close")
OUTLINE = (
    "hook: the whole alternator, closed, the pulley turning",
    "body part 1: the housing opens; the belt-driven pulley turns the claw-pole rotor, and its "
    "magnetic field appears",
    "body part 2: the stator's copper coils around the spinning rotor light up in turn: "
    "three-phase alternating current",
    "body part 3: the 6 rectifier diodes turn it into direct current, which leaves through the "
    "output terminal",
    "Engineer's Note: a slow look around the opened alternator",
    "close: the alternator closes again, the pulley still turning",
)

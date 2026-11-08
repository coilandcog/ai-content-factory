"""Blender model: a car alternator (D-121, D-123).

The 3D model itself is `bpy/alternator.py` (it only runs inside Blender, on the render PC). This
file is what the factory needs on the laptop: the verified facts the narration may use, and the
film's scenes in order, so the script follows what the film shows.
"""

from __future__ import annotations

from factory.blender import Template
from factory.blueprint.canvas import Fact

MACHINE = "alternator"
MODEL_FILE = "alternator.py"  # in factory/blender/job_files/
SHORTS = True  # it has the 6-scene Short film (OUTLINE, ROLES, film())

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
# What a body sentence must name for its scene to start there (D-126); None = placed by time.
SCENE_KEYWORDS = (None, r"rotor|belt|field coil", r"stator|winding|induc", r"diode|rectif", None,
                  None)  # fmt: skip
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

# --- long videos (D-129) -------------------------------------------------------------------------
# The model's parts, as the narration names them. A sentence that names a part its scene doesn't
# show is refused, so the picture can't contradict the words. Words that are not model parts
# (engine, belt, battery) are context and always allowed.
PARTS: dict[str, str] = {
    "pulley": r"\bpulleys?\b",
    "fan": r"\bfans?\b",
    "housing": r"\bhousings?\b|\bcasing\b",
    "shaft": r"\bshafts?\b",
    "rotor": r"\brotors?\b|\bclaw",
    "field coil": r"\bfield (?:coil|winding)s?\b",
    "slip rings": r"\bslip rings?\b",
    "brushes": r"\bbrush(?:es)?\b",
    # "winding" alone can be the rotor's field coil or the stator's: only names that say which
    # count (Gemini called the field coil "the internal wire winding", 2026-11-08).
    "stator": r"\bstators?\b|\b(?:stator|three-phase|output) windings?\b",
    "diodes": r"\bdiodes?\b|\brectifiers?\b",
    "regulator": r"\bregulators?\b",
    "output terminal": r"\boutput terminal\b|\bB\+",
}

# The views a long video's scenes can use.
TEMPLATES: dict[str, Template] = {
    "whole": Template(("pulley", "fan", "housing", "shaft"),
                      "the closed alternator, pulley turning: what it is and where it sits"),
    "exploded": Template(("*",), "every part pulled apart in order: the parts and how they fit"),
    "rotor": Template(("rotor", "field coil", "slip rings", "shaft", "pulley", "fan", "housing"),
                      "the spinning rotor: its field coil makes it an electromagnet"),
    "stator": Template(("stator", "rotor", "field coil", "housing"),
                       "the stator windings around the rotor: three-phase alternating current"),
    "rectifier": Template(("diodes", "regulator", "output terminal", "housing"),
                          "the 6 diodes and the regulator: direct current at a steady voltage"),
    "brushes": Template(("brushes", "slip rings", "rotor", "shaft", "field coil", "housing"),
                        "the carbon brushes on the slip rings: how current reaches the rotor"),
    "note": Template(("*",), "the opened alternator: the owner's Engineer's Note"),
}  # fmt: skip

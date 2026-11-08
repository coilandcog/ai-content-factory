"""Blender model: a car starter motor (ROADMAP 2.35, D-131).

The 3D model itself is `job_files/starter_motor.py` (it only runs inside Blender). This file is
what the factory needs: the verified facts, the parts as the narration names them, and the views
(templates) a long video can use. Shorts of this machine come later: it has no Short film yet.
"""

from __future__ import annotations

from factory.blender import Template
from factory.blueprint.canvas import Fact

MACHINE = "starter motor"
MODEL_FILE = "starter_motor.py"  # in factory/blender/job_files/
SHORTS = False  # no 6-scene Short film yet: long videos only

WIKI = "https://en.wikipedia.org/wiki/Starter_(engine)"
PEARSON = "https://www.pearsonhighered.com/assets/samplechapter/0/1/3/2/0132373629.pdf"
HALDERMAN = "https://www.slideshare.net/slideshow/halderman-ch052-lecture/9501694"
ROADRUNNER = "https://www.roadrunner-starters.com/pages/starter-motors-a-brief-history-lesson"

# Every fact has two independent pages that agree (D-122); checked 2026-11-08. Where they give
# different values, the fact is the range that covers both (D-111 rule).
FACTS: dict[str, Fact] = {
    "ring_ratio": Fact(
        "15 to 20 times",
        PEARSON,
        about="teeth on the flywheel ring gear compared with the starter pinion",
        second_source=HALDERMAN,
    ),
    "reduction": Fact(
        "2.5 to 4.5 times",
        ROADRUNNER,
        about="gear reduction between the armature and the pinion in a gear-reduction starter",
        second_source=WIKI,
    ),
}

# The model's parts, as the narration names them (D-129). Words that are not model parts
# (engine, battery, key, crankshaft) are context and always allowed.
PARTS: dict[str, str] = {
    "housing": r"\bhousings?\b|\bcasing\b|\byoke\b",
    "magnets": r"\bmagnets?\b|\bpole shoes?\b|\bfield (?:coil|winding)s?\b",
    "armature": r"\barmatures?\b",
    "commutator": r"\bcommutators?\b",
    "brushes": r"\bbrush(?:es)?\b",
    "shaft": r"\bshafts?\b",
    "planetary gears": r"\bplanet(?:ary)?\b|\bsun gear\b|\breduction gears?\b",
    "solenoid": r"\bsolenoids?\b",
    "plunger": r"\bplungers?\b",
    "contacts": r"\bcontacts?\b|\bterminals?\b",
    "lever": r"\blevers?\b|\bfork\b",
    "clutch": r"\bclutch(?:es)?\b|\brollers?\b|\bsprag\b",
    "pinion": r"\bpinions?\b",
    "ring gear": r"\bring gear\b|\bflywheel\b",
}

TEMPLATES: dict[str, Template] = {
    "whole": Template(("housing", "solenoid", "pinion", "shaft", "ring gear"),
                      "the closed starter motor next to the engine's flywheel ring gear"),
    "exploded": Template(("*",), "every part pulled apart along the shaft"),
    "armature": Template(("armature", "commutator", "brushes", "magnets", "shaft"),
                         "the spinning armature between the magnets, fed through brushes"),
    "solenoid": Template(("solenoid", "plunger", "contacts", "lever", "pinion", "clutch"),
                         "the solenoid pulls its plunger: the lever pushes the pinion out and "
                         "the contacts close"),
    "pinion": Template(("pinion", "ring gear", "clutch", "shaft", "lever"),
                       "the small pinion meshing with the big flywheel ring gear"),
    "clutch": Template(("clutch", "pinion", "ring gear", "shaft"),
                       "the one-way clutch: drive goes out, never back in"),
    "gears": Template(("planetary gears", "armature", "shaft", "housing"),
                      "the planetary gears trading speed for turning force"),
    "note": Template(("*",), "the opened starter motor: the owner's Engineer's Note"),
}  # fmt: skip

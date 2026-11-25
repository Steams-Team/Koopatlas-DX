"""
kdx/routeactions.py - the game's route actions: what column 1 of routeWn.csv makes Mario do on a segment.

Reverse engineered from NSMBUDX v1.0.0 (nsmbud-reverse FindingsG/WORLDMAP_ROUTES.md, ledger F0311-F0316):
`CourseSelectCsvData::ReadAction_` (0x60180FE8) matches the name against a 23-entry table and stores the player's
`CourseSelectPlayerAnim::AnimType`. `CourseSelectPlayer2P::setAction_` (0x601B83F8) plays that type's animation:
walk surfaces run (anim 2) and differ by footstep sound, slopes slide, ladders and vines climb, jump jumps. The game
reads nothing else per route (column 2 is skipped), so these 23 are every movement a vanilla route can have.

    for a in ACTIONS: a.name, a.label, a.style, a.anim_type, a.speed
    action("ジャンプ").style == "jump"
"""
from __future__ import annotations

from dataclasses import dataclass

STYLES = {
    "walk": "Walk",
    "slide": "Slide",
    "climb": "Climb",
    "jump": "Jump",
}


@dataclass(frozen=True)
class Action:
    name: str            # what the CSV holds (Japanese, as the game spells it)
    label: str           # English
    anim_type: int       # CourseSelectPlayerAnim::AnimType the game stores
    style: str           # walk / slide / climb / jump
    group: str           # palette group
    colour: str          # editor colour
    sound: str           # footstep sound family
    speed: float         # animation rate (AnimInfo.rate, default player)
    dust: bool           # run dust (initRunEffect_)
    facing: str = ""     # climbs: "", "left", "right" (getNetWalkDirAngle_)
    vanilla: bool = True # used in a vanilla world
    note: str = ""

    @property
    def description(self):
        how = {"walk": "Mario walks", "slide": "Mario slides down", "climb": "Mario climbs",
               "jump": "Mario jumps across in an arc"}[self.style]
        bits = [how]
        if self.facing:
            bits.append("facing 45° %s" % self.facing)
        if self.style == "walk":
            bits.append("%s footsteps" % self.sound)
            if not self.dust:
                bits.append("no dust")
        text = "%s (%s): %s." % (self.label, self.name, ", ".join(bits))
        if self.note:
            text += " " + self.note
        if not self.vanilla:
            text += " Not used by any vanilla world, but the game supports it."
        return text


ACTIONS = (
    Action("道", "Road", 3, "walk", "Ground", "#e8c27a", "grass", 3.5, True),
    Action("土", "Dirt", 28, "walk", "Ground", "#b0814f", "grass", 3.5, True, vanilla=False),
    Action("砂", "Sand", 20, "walk", "Ground", "#f2d98c", "sand", 3.5, True),
    Action("流砂", "Quicksand", 21, "walk", "Ground", "#d9a85b", "blown sand", 3.5, True, vanilla=False),
    Action("石", "Stone", 5, "walk", "Ground", "#a8b0bd", "rock", 4.0, True),
    Action("雪", "Snow", 22, "walk", "Ground", "#f1f5f9", "snow", 3.5, True),
    Action("氷", "Ice", 23, "walk", "Ground", "#9fdcf5", "rock", 3.5, True),
    Action("雲", "Cloud", 24, "walk", "Sky and water", "#ffffff", "cloud", 3.5, True),
    Action("水", "Water", 25, "walk", "Sky and water", "#4fa3f7", "water", 3.5, True, vanilla=False,
           note="Walks with splashing steps (the game has no swim on the map)."),
    Action("スイッチブロック", "Switch block", 19, "walk", "Sky and water", "#f59e0b", "grass", 3.5, True, vanilla=False),
    Action("木", "Wood bridge", 33, "walk", "Bridges", "#c08a52", "wood", 3.5, True),
    Action("石橋", "Stone bridge", 4, "walk", "Bridges", "#8b95a7", "rock", 4.0, False),
    Action("坂", "Slope", 11, "slide", "Slopes", "#86c06c", "slip", 1.0, False),
    Action("氷坂", "Ice slope", 12, "slide", "Slopes", "#7dd3fc", "ice slip", 1.0, False),
    Action("はしご", "Ladder", 6, "climb", "Climb", "#d6a35c", "ladder", 2.5, False),
    Action("はしご左", "Ladder (left)", 26, "climb", "Climb", "#d6a35c", "ladder", 2.5, False, "left", vanilla=False),
    Action("はしご右", "Ladder (right)", 27, "climb", "Climb", "#d6a35c", "ladder", 2.5, False, "right"),
    Action("はしご岩", "Rock ladder", 29, "climb", "Climb", "#9ca3af", "rock ladder", 2.5, False),
    Action("はしご岩左", "Rock ladder (left)", 30, "climb", "Climb", "#9ca3af", "rock ladder", 2.5, False, "left"),
    Action("はしご岩右", "Rock ladder (right)", 31, "climb", "Climb", "#9ca3af", "rock ladder", 2.5, False, "right"),
    Action("はしご縄", "Rope ladder", 32, "climb", "Climb", "#c4a484", "rope", 2.5, False, vanilla=False),
    Action("ツタ", "Vine", 10, "climb", "Climb", "#4ade80", "vine", 1.5, False, note="Beanstalk climb animation."),
    Action("ジャンプ", "Jump", 7, "jump", "Jump", "#f472b6", "none", 1.0, False,
           note="For gaps, cliffs and stepping stones."),
)
BY_NAME = {a.name: a for a in ACTIONS}
GROUPS = tuple(dict.fromkeys(a.group for a in ACTIONS))
DEFAULT = BY_NAME["道"]


def action(name):
    """The Action for a CSV name; an unknown name walks as road in the game (ReadAction_ leaves it unset)."""
    return BY_NAME.get(name, DEFAULT)


def known(name):
    return name in BY_NAME


def walk_speed(name):
    """Relative speed for the editor's walk test: tiles per second at 6 for road."""
    a = action(name)
    return {"walk": 6.0, "slide": 9.0, "climb": 3.0, "jump": 5.0}[a.style] * (a.speed / 3.5 if a.style == "walk" else 1.0)

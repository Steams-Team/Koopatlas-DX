"""
kdx/flaghelp.py - every point flag in plain words: a name, a group, what it does, where vanilla uses it, how sure we are.

Sources: NSMBUDX 1.0.0's code (nsmbud-reverse FindingsG; CourseSelectRouteLib::check*Flag readers), vanilla Deluxe
point tables, and the NSMBU (Wii U) modding wiki. `sure` is "code" (read by Deluxe code we traced), "vanilla"
(inferred from where Deluxe's own maps use it), "wiki" (only the Wii U wiki says so: not checked on Deluxe) or
"unknown".

    info("stop").label -> "Stop here"
    grouped() -> [(group, [FlagInfo...])]
"""
from __future__ import annotations

from dataclasses import dataclass

from . import rules


@dataclass(frozen=True)
class FlagInfo:
    name: str
    label: str
    group: str
    text: str
    sure: str = "vanilla"           # code / vanilla / unknown
    worlds: tuple = ()              # slots whose code reads it (empty: any map)
    warn: str = ""                  # what breaks if misused


GROUPS = ("Walking", "Camera", "Pipes and links", "Map enemies", "Yoshi and rides", "Events and scenery", "Other")

_F = [
    # walking
    FlagInfo("stop", "Stop here", "Walking", "Mario always stops on this point, even when a path goes straight through. "
             "Use it on corners and junctions you want the player to pause at.", "code"),
    FlagInfo("onestop", "Pause, then go on", "Walking", "Mario stops for a moment and then may walk on, as on W6's "
             "moving platforms.", "code"),
    FlagInfo("nonstop", "Never stop", "Walking", "Mario walks straight past, never stopping here (W4's Cooligan "
             "circle)."),
    FlagInfo("merge", "Paths join", "Walking", "Where paths (and ride rails) meet. Deluxe reads it in the lift-train, "
             "ride and move-setup code (CourseSelectLineTrain, entryMoveInfo_); vanilla puts it on 15 junctions.",
             "code"),
    FlagInfo("restart", "Ambush restart", "Walking", "Where Mario is put back (read by updatePos_), e.g. after losing "
             "to a map enemy. Put it on the stops around each enemy (28 vanilla points).", "code"),
    FlagInfo("water", "Water landing", "Walking", "On W3's water points. The Wii U wiki says landing here plays a "
             "splash; not checked on Deluxe.", "wiki"),
    # camera
    FlagInfo("focus", "Camera looks here", "Camera", "The camera turns to frame this point while Mario is near it "
             "(11 vanilla points, often at lookouts)."),
    FlagInfo("fix", "fix (ignored)", "Other", "Vanilla W1 F001 carries it, but it is not in the game's flag tables, so "
             "the game ignores it. Use fixX / fixY / fixZ instead.", "code"),
    FlagInfo("fixX", "Camera fixed sideways", "Camera", "The camera keeps its left-right position here (W5b's "
             "Boo house)."),
    FlagInfo("fixY", "Camera fixed in height", "Camera", "The camera keeps its height here (W1 W1K1)."),
    FlagInfo("fixZ", "Camera fixed in depth", "Camera", "The camera keeps its front-back position here. Not used in "
             "vanilla."),
    FlagInfo("scale", "Camera zoom", "Camera", "Camera related; not used in vanilla.", "unknown"),
    # pipes and links
    FlagInfo("dokan", "Warp pipe", "Pipes and links", "The world's warp pipe. Mario steps in and picks any world he has "
             "reached (or World 1's pipe island). One per world: only the first counts.", "code"),
    FlagInfo("dokan2", "Island pipe", "Pipes and links", "World 1's pipe-island pipe. The warp to the island lands on "
             "the point named Fs01.", "code", ("W1",)),
    FlagInfo("dokanW8", "W8 pipe", "Pipes and links", "World 8's pipe point (Fd08); no route uses it.", "unknown",
             ("W8a",)),
    *[FlagInfo("link%d" % k, "Link %d (a ↔ b map)" % k, "Pipes and links", "Joins this point to the point with the "
               "same flag in the other half of the world (W5a ↔ W5b, W8a ↔ W8b).", "code") for k in range(1, 6)],
    # map enemies
    FlagInfo("W1PakkunA", "W1 Piranha Plant", "Map enemies", "The World 1 Piranha Plant sits here. Walking onto this "
             "point starts its ambush: Mario jumps at it instead of walking or climbing. Only one per map.", "code",
             ("W1",), "Required in W1 (the game crashes without it). Don't end a climb on it."),
    FlagInfo("W1PakkunB", "W1 Piranha Plant B", "Map enemies", "A second Piranha Plant slot; unused, and a second "
             "plant crashes the game when stepped on.", "unknown", ("W1",)),
    FlagInfo("W1Item", "W1 item enemy", "Map enemies", "The World 1 map enemy that carries an item starts here.",
             "code", ("W1",), "Required in W1 (the game crashes without it)."),
    FlagInfo("W2Enemy0", "W2 enemy path 0", "Map enemies", "Points World 2's wandering enemies walk between (route 0)."),
    FlagInfo("W2Enemy1", "W2 enemy path 1", "Map enemies", "Points World 2's wandering enemies walk between (route 1)."),
    *[FlagInfo("W2Enemy" + c, "W2 enemy %s start" % c, "Map enemies", "A World 2 map enemy starts here.", "code",
               ("W2a",), "Required in W2 (crash without it).") for c in "ABC"],
    FlagInfo("W2maze", "W2 maze", "Map enemies", "World 2's desert maze: map enemies only walk between these points."),
    *[FlagInfo("W3Enemy" + c, "W3 enemy %s start" % c, "Map enemies", "A World 3 map enemy starts here.", "code",
               ("W3",), "Required in W3 (crash without it).") for c in "AB"],
    *[FlagInfo("W4Enemy" + c, "W4 enemy %s start" % c, "Map enemies", "A World 4 map enemy starts here.", "code",
               ("W4",), "Required in W4 (crash without it).") for c in "ABCD"],
    FlagInfo("W4Circle", "W4 Cooligan circle", "Map enemies", "The loop World 4's Cooligan and acorn item skate "
             "around."),
    *[FlagInfo("W5bEnemy%d" % k, "W5b enemy %d" % k, "Map enemies", "A Boo in World 5's haunted half starts here.",
               "code" if k <= 3 else "vanilla", ("W5b",), "Required in W5b (crash without it)." if k <= 3 else "")
      for k in range(1, 8)],
    *[FlagInfo("Chorobon" + c, "W7 Fuzzy %s" % c, "Map enemies", "A World 7 Fuzzy starts here.", "code", ("W7",),
               "Required in W7 (crash without it).") for c in "ABC"],
    # yoshi and rides
    FlagInfo("balloon1", "Balloon Baby Yoshi (W1)", "Yoshi and rides", "Where World 1's balloon Baby Yoshi waits.",
             "code", ("W1",), "Required in W1, or the map freezes when Mario walks back to his start."),
    FlagInfo("balloon2", "Balloon Baby Yoshi (W3)", "Yoshi and rides", "Where World 3's balloon Baby Yoshi waits.",
             "code", ("W3",), "Required in W3."),
    FlagInfo("bubble1", "Bubble Baby Yoshi (W4)", "Yoshi and rides", "Where World 4's bubble Baby Yoshi waits.",
             "code", ("W4",), "Required in W4."),
    FlagInfo("bubble2", "Bubble Baby Yoshi (W6)", "Yoshi and rides", "Where World 6's bubble Baby Yoshi waits.",
             "code", ("W6a",), "Required in W6."),
    FlagInfo("boarding", "Lift: get on", "Yoshi and rides", "Mario boards the spine coaster / lift here (W4, W6)."),
    FlagInfo("station", "Lift: station", "Yoshi and rides", "The lift's station (W4, W6)."),
    FlagInfo("coaster", "Lift: track", "Yoshi and rides", "Where the spine coaster model sits (W4, W6)."),
    # events and scenery
    FlagInfo("board1", "Signpost", "Events and scenery", "A signpost read when Mario arrives at this stop "
             "(checkBoardFlag in checkChangeStateArrivedAtStopPoint_); vanilla W2 F007 shows the way to Worlds 3 and 4.",
             "code"),
    FlagInfo("noUI", "Hide menu buttons", "Events and scenery", "On W4's Cooligan loop and W5b's Boo points. The Wii U "
             "wiki says it hides Menu, Map View and Items; not checked on Deluxe.", "wiki"),
    *[FlagInfo("Kanketsusen" + c, "W3 water spout %s" % c, "Events and scenery", "A World 3 water spout that turns "
               "on and off; while it blocks the way Mario can't walk on (RouteMgr::checkMovable).", "code", ("W3",))
      for c in "ABDE"],
    *[FlagInfo("Rainbow" + c, "W7 rainbow %s" % c, "Events and scenery", "Part of a World 7 rainbow bridge that "
               "appears in its cutscene.", "vanilla", ("W7",)) for c in "ABCD"],
    FlagInfo("W7Demo", "W7 rainbow cutscene", "Events and scenery", "Starts World 7's rainbow cutscene.",
             "vanilla", ("W7",)),
    FlagInfo("W7PreDemo", "W7 before rainbows", "Events and scenery", "Puts World 7's rainbows back to before the "
             "cutscene.", "vanilla", ("W7",)),
    FlagInfo("W7DemoEnd", "W7 cutscene end", "Events and scenery", "Ends World 7's rainbow cutscene.", "vanilla",
             ("W7",)),
    FlagInfo("W7Return", "W7 rainbow exit", "Events and scenery", "Where Mario leaves World 7's rainbow area "
             "(read by procInOutRainbowArea_ and on arrival at stops).", "code", ("W7",)),
    FlagInfo("W5bDemo", "Boo cutscene", "Events and scenery", "Triggers World 5's Boo cutscene.", "vanilla",
             ("W5b",)),
    FlagInfo("W5bPreDemo", "Before Boo cutscene", "Events and scenery", "Puts World 5's Boo cutscene back.",
             "vanilla", ("W5b",)),
    FlagInfo("W5Teresa", "W5 Boo", "Events and scenery", "World 5's Boo point (Ftrs).", "unknown", ("W5b",)),
    FlagInfo("shadowDemo", "Nabbit cutscene", "Events and scenery", "Nabbit's cutscene point (W2, W5).", "vanilla"),
    FlagInfo("shadowRoute", "Nabbit route", "Events and scenery", "Nabbit's escape route (W2).", "vanilla"),
    FlagInfo("switchA", "W6 switch A", "Events and scenery", "A ? switch (on a stop point). Stepping on it while it is live flips World 6's switch, so the other side's switches become live; it very likely also swaps the red/blue trap blocks (F0329).", "vanilla", ("W6a",)),
    FlagInfo("switchB", "W6 switch B", "Events and scenery", "A ? switch (on a stop point), the other side of switch A: live while the A side is down (F0329).", "vanilla", ("W6a",)),
    FlagInfo("W6trapA", "W6 trap A", "Events and scenery", "A red trap block. The road between two trapA points is blocked while the blocks are up (trap state 0); a switch flips them and plays the 'trapA'/'trapB' animation (F0328).", "vanilla", ("W6a",)),
    FlagInfo("W6trapB", "W6 trap B", "Events and scenery", "A blue trap block. The road between two trapB points is blocked while the trap state is 1, the opposite of trapA (F0328).", "vanilla", ("W6a",)),
    FlagInfo("egg", "No Yoshi past here", "Yoshi and rides", "The map Yoshi may not come along past this point "
             "(checkBanningEggFlag, read while walking). Vanilla: W6 F011.", "code"),
    FlagInfo("KoopaJr01", "Bowser Jr.", "Events and scenery", "Bowser Jr.'s point (W8b).", "vanilla"),
    *[FlagInfo("ShipKoopaJr0%d" % k, "Airship %d" % k, "Events and scenery", "Where Bowser Jr.'s airship waits "
               "(W5, W7, W8).", "vanilla") for k in range(1, 5)],
]
_F += [FlagInfo("1Up", "Toad house: 1-Up", "Events and scenery", "Makes this Toad house the 1-Up kind: the flag "
                "changes the house's course number (getCourseInCourseNo).", "code"),
       FlagInfo("Shuffle", "Toad house: card shuffle", "Events and scenery", "Makes this Toad house the card-shuffle "
                "minigame (getCourseInCourseNo).", "code"),
       FlagInfo("Slot", "Toad house: slots", "Events and scenery", "Makes this Toad house the slot minigame "
                "(getCourseInCourseNo).", "code")]
_OTHER = {"clear": "Clear", "W3TrapA": "W3 trap A",
          "W3TrapB": "W3 trap B", "W4TrapA": "W4 trap A", "W4TrapB": "W4 trap B", "W8Koopa01": "W8 Bowser 1",
          "W8Koopa02": "W8 Bowser 2", "W8Koopa03": "W8 Bowser 3"}
_F += [FlagInfo(n, l, "Other", "The game knows this name but no vanilla map uses it; what it does is unknown.",
                "unknown") for n, l in _OTHER.items()]
FLAGS = {f.name: f for f in _F}
for _n in sorted(rules.KNOWN_FLAGS - set(FLAGS)):
    FLAGS[_n] = FlagInfo(_n, _n, "Other", "Known to the game; what it does is not documented yet.", "unknown")

SURE_TEXT = {"code": "read by Deluxe's code (traced)", "vanilla": "inferred from Deluxe's own maps",
             "wiki": "only the Wii U wiki says so: not yet checked on Deluxe", "unknown": "not understood yet"}


def info(name):
    return FLAGS.get(name) or FlagInfo(name, name, "Other", "The game does not know this name and ignores it.",
                                       "unknown", warn="Unknown flag: the game ignores it.")


def tooltip(name, slot=None):
    f = info(name)
    out = "<b>%s</b> <span style='color:gray'>(%s)</span><br>%s" % (f.label, f.name, f.text)
    if f.warn:
        out += "<br><span style='color:#f59e0b'>%s</span>" % f.warn
    if f.worlds and slot and slot not in f.worlds:
        out += "<br><span style='color:#ef4444'>Only read in %s: here it does nothing.</span>" % ", ".join(f.worlds)
    return out + "<br><i>%s</i>" % SURE_TEXT[f.sure]


def grouped(names=None):
    names = list(FLAGS) if names is None else names
    return [(g, [info(n) for n in names if info(n).group == g]) for g in GROUPS
            if any(info(n).group == g for n in names)]


def summary(flags):
    """'Stop here, Ambush restart' for a flag list."""
    return ", ".join(info(f).label for f in flags)

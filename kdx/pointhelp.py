"""
kdx/pointhelp.py - what a map point is, in plain words, from its name (the game reads meaning from names) and flags.

    explain("W103", ["restart"], "Yoshi Hill") -> "Level 1-3 (Yoshi Hill) ..."
    free_level_names(project_nodes, world) -> ["W106", ...]   the next names for new level plots
"""
from __future__ import annotations

from . import rules

_KIND = {   # third character of a W point -> what it is (WorldMgr::getCourseNoFromPointName 0x6040D864)
    "0": "Level", "C": "Castle", "T": "Tower", "G": "Ghost house", "A": "Airship", "S": "Cannon / secret level",
    "M": "Start point", "K": "Toad house", "X": "Toad house (bonus)", "W": "World exit", "J": "Entry from another world",
    "B": "Bonus level",
}


def explain(pid, flags=(), name=None):
    """One or two sentences for the properties panel."""
    flags = list(flags or [])
    out = []
    if len(pid) == 4 and pid[0] == "W" and pid[1:2].isalnum():
        kind = _KIND.get(pid[2], "World point")
        lf = rules.level_file(pid)
        num = lf[len("Course/"):-len(".sarc")] if lf else None
        s = "%s%s%s." % (kind, " " + num if num and kind in ("Level", "Bonus level") else "",
                         " (%s)" % name if name else "")
        if lf:
            s += " Mario stops here and can enter %s." % lf
        if pid[2] == "M":
            s += " Mario starts the world here."
        if pid[2] == "J":
            s += " Where Mario arrives from another world's path."
        if pid[2] == "S" and pid[3:] == "0":
            s += " Also the world's cannon when its normal exit opens a path into a later world."
        out.append(s)
    elif pid[:1] == "K":
        out.append("A bend: the road turns here. Mario walks through without stopping. Bends can't have flags.")
    elif pid[:1] == "F":
        sub = {"d": "a pipe point", "a": "an ambush point", "b": "an ambush point", "c": "an ambush or climb point",
               "l": "a link point", "s": "a stop", "j": "a junction", "y": "a Yoshi spot"}.get(pid[1:2], "")
        out.append("A free point%s. Mario walks through unless a flag says otherwise." % (" (" + sub + ")" if sub else ""))
    else:
        out.append("A point the game reads by name.")
    if "stop" in flags and pid[:1] == "F":
        out.append("It is a stop: Mario always pauses here.")
    from . import flaghelp
    warns = [flaghelp.info(f).warn for f in flags if flaghelp.info(f).warn]
    if warns:
        out.append(warns[0])
    return " ".join(out)


def free_level_names(nodes, world, count=4):
    """The next unused level names of a world (W<w>01 … W<w>0f), for new level plots."""
    out = []
    for n in range(1, 16):
        cand = "W%x0%x" % (world, n)
        if cand not in nodes:
            out.append(cand)
            if len(out) == count:
                break
    return out

"""
kdx/pointactions.py - what a map point does beyond being stood on: pipes, cannons, links, rides.

Reverse engineered from NSMBUDX v1.0.0 (nsmbud-reverse FindingsG/WORLDMAP_ROUTES.md, F0317-F0321):
  warp pipe   `dokan` (Flag3 bit 10). Every world's warp pipe joins one network: Mario picks any world he has reached
              and is shot to its start, or to World 1's pipe island (the point named Fs01). Only the first `dokan`
              point of a map counts (getWarpDokanFlagPos 0x601D9F00).
  island pipe `dokan2` (bit 11), World 1 only: the pipe on the island; the warp to the island lands on `Fs01`.
  cannon      a point named W<n>S0: its normal exit opens a chain of paths into a later world and Mario is shot
              along the first one (CourseSelectWarpCannon).
  link        link1-link5: joins a point of a world's a map to the same-flagged point of its b map (W5, W8).
  map Yoshi   balloon1/2, bubble1/2: where the map Yoshi waits (W1, W3, W4, W6).
  lift ride   boarding / station / coaster (W4, W6).

    for a in POINT_ACTIONS: a.key, a.label, a.flag
    actions_of("Fd01", ["dokan"]) -> [POINT["warp_pipe"]]
"""
from __future__ import annotations

from dataclasses import dataclass

from . import rules


@dataclass(frozen=True)
class PointAction:
    key: str
    label: str
    flag: str            # the flag that makes it ("" for a naming rule)
    colour: str
    description: str
    worlds: tuple = ()   # slots where the game uses it (empty: any)


POINT_ACTIONS = (
    PointAction("warp_pipe", "Warp pipe", "dokan", "#22c55e",
                "The world's warp pipe: Mario steps in, picks any world he has reached and is shot to its start (or to "
                "World 1's pipe island). One per world: only the first point flagged dokan counts."),
    PointAction("island_pipe", "Island pipe", "dokan2", "#16a34a",
                "World 1's pipe island: its own pipe. Warping to the island lands on the point named Fs01, so the "
                "island needs one.", ("W1",)),
    PointAction("cannon", "Cannon", "", "#374151",
                "A cannon (a point named W<n>S0): once its path opens, Mario walks onto it and is shot to a later "
                "world along the first path its normal exit opens (W1S0 → W3J0 in the game)."),
    PointAction("link", "Link (a ↔ b map)", "link1", "#38bdf8",
                "Joins this point to the point with the same link flag (link1-link5) in the other half of the world "
                "(W5a ↔ W5b, W8a ↔ W8b).", ("W5a", "W5b", "W8a", "W8b")),
    PointAction("yoshi", "Map Yoshi spot", "balloon1", "#4ade80",
                "Where the map Yoshi waits: balloon1 (W1), balloon2 (W3), bubble1 (W4), bubble2 (W6). The world must "
                "have its own, or the map freezes.", ("W1", "W3", "W4", "W6")),
    PointAction("lift", "Lift ride", "boarding", "#f59e0b",
                "The lift ride: boarding, station and coaster points (W4, W6).", ("W4", "W6")),
)
POINT = {a.key: a for a in POINT_ACTIONS}
YOSHI_FLAGS = {"W1": "balloon1", "W3": "balloon2", "W4": "bubble1", "W6": "bubble2"}
LINK_FLAGS = ("link1", "link2", "link3", "link4", "link5")
LIFT_FLAGS = ("boarding", "station", "coaster")


def is_cannon(pid):
    return len(pid) == 4 and pid[0] == "W" and pid[2:4] == "S0"


def actions_of(pid, flags):
    """The point actions a point has (its name and flag list)."""
    flags = list(flags or [])
    out = []
    if "dokan" in flags:
        out.append(POINT["warp_pipe"])
    if "dokan2" in flags:
        out.append(POINT["island_pipe"])
    if is_cannon(pid):
        out.append(POINT["cannon"])
    if any(f in LINK_FLAGS for f in flags):
        out.append(POINT["link"])
    if any(f in YOSHI_FLAGS.values() for f in flags):
        out.append(POINT["yoshi"])
    if any(f in LIFT_FLAGS for f in flags):
        out.append(POINT["lift"])
    return out


def cannon_target(row):
    """(world number, entry point) a cannon shoots Mario to: the far end of the first path its normal exit opens."""
    paths = rules.paths(row[4] if len(row) > 4 else "")
    for p in paths:
        ends = rules.route_ends(p)
        if ends and is_cannon(ends[0]):
            far = ends[1]                                    # W3J0 -> world 3; a junction such as Fj02 -> None
            world = int(far[1], 16) if far[:1] == "W" and far[1:2] in "123456789abcdef" and far[1:2] else None
            return world, far
    return None, None


def problems(point_rows, slot=None):
    """Warnings about pipes, cannons and links in a map's point table: [text]."""
    out = []
    flags = {r[1]: rules.split(r[2]) for r in point_rows if len(r) > 2}
    names = set(flags)
    pipes = [p for p, f in flags.items() if "dokan" in f]
    if len(pipes) > 1:
        out.append("Warp pipes %s: the game uses only the first one (%s), the others are plain points."
                   % (", ".join(pipes), pipes[0]))
    islands = [p for p, f in flags.items() if "dokan2" in f]
    if islands and slot not in (None, "W1"):
        out.append("%s is flagged dokan2 (World 1's island pipe): in %s the game ignores it." % (islands[0], slot))
    if islands and (slot in (None, "W1")) and "Fs01" not in names:
        out.append("%s is World 1's island pipe, but the map has no point named Fs01: warping to the island lands "
                   "there (0x601E06F0)." % islands[0])
    for p in names:
        if is_cannon(p):
            row = next(r for r in point_rows if len(r) > 1 and r[1] == p)
            if not any(rules.split(c) for c in row[3:5]):    # an empty W<n>S0 is a plain (often hidden) point
                continue
            w, far = cannon_target(row)
            if far is None:
                out.append("Cannon %s opens no path that starts at it: there is nowhere to shoot Mario to." % p)
    return out

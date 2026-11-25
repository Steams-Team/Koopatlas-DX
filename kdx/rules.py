"""
kdx/rules.py - what the game accepts and how it reads a world map.

Every rule here comes from the reverse engineering of Deluxe v1.0.0 in scripts/worldmap/wmtool.py and docs/WORLDMAP.md
(ledger F0064-F0075, readable/course_select/WorldMapData.cpp). Koopatlas DX uses the same logic so that its checks and
wmtool's agree.
"""
from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict, deque

# ---- limits (WorldMapData::load 0x6017BE9C and helpers) ----------------------------------------------------------------
MAX_POINT_CSV = 4095        # read through a 4096-byte string buffer
MAX_ROUTE_CSV = 8191        # 0x2000-byte buffer
MAX_POINTS = 256
MAX_ROUTES = 256
MAX_UNLOCK_NODES = 4        # per exit; a 5th wraps onto slot 0
MAX_UNLOCK_ROUTES = 16
MAX_REVEALS = 2             # columns 5 / 8
MAX_SAVED_F_POINTS = 128    # save slots per world/variant
MAX_COURSES = 58            # course flags per world

# ---- point table columns -----------------------------------------------------------------------------------------------
COLUMNS = {
    0: "Index", 1: "Name", 2: "Flags",
    3: "Nodes opened (normal exit)", 4: "Paths opened (normal exit)", 5: "Appear animations (normal exit)",
    6: "Nodes opened (secret exit)", 7: "Paths opened (secret exit)", 8: "Appear animations (secret exit)",
}
COLUMN_LIMITS = {3: MAX_UNLOCK_NODES, 4: MAX_UNLOCK_ROUTES, 5: MAX_REVEALS, 6: MAX_UNLOCK_NODES, 7: MAX_UNLOCK_ROUTES, 8: MAX_REVEALS}

# ---- flags (tables built at 0x60183BE8 / F9C / 4350 / 4704) ----------------------------------------------------------------
FLAG_TABLES = {
    "A": "stop,link1,link2,link3,link4,link5,scale,fixX,fixY,fixZ,focus,board1,Shuffle,Slot,1Up,nonstop,onestop,merge",
    "B": "W1PakkunA,W1PakkunB,W1Item,W2Enemy0,W2Enemy1,W2EnemyA,W2EnemyB,W2EnemyC,W2maze,W3EnemyA,W3EnemyB,W4EnemyA,"
         "W4EnemyB,W4EnemyC,W4EnemyD,W4Circle,ChorobonA,ChorobonB,ChorobonC,W5bEnemy1,W5bEnemy2,W5bEnemy3,W5bEnemy4,"
         "W5bEnemy5,W5bEnemy6,W5bEnemy7,W5bDemo,W5bPreDemo,W5Teresa,noUI,restart",
    "C": "W3TrapA,W3TrapB,W4TrapA,W4TrapB,W6trapA,W6trapB,KanketsusenA,KanketsusenB,KanketsusenD,KanketsusenE,dokan,"
         "dokan2,RainbowA,RainbowB,RainbowC,RainbowD,clear,switchA,switchB,ShipKoopaJr01,ShipKoopaJr02,ShipKoopaJr03,"
         "ShipKoopaJr04,W8Koopa01,W8Koopa02,W8Koopa03,egg,W7Demo,W7PreDemo,W7DemoEnd,W7Return,dokanW8",
    "D": "boarding,station,coaster,shadowDemo,shadowRoute,KoopaJr01,W1Item,balloon1,balloon2,bubble1,bubble2,water",
}
KNOWN_FLAGS = {f for names in FLAG_TABLES.values() for f in names.split(",")}
FLAG_HINTS = {          # inferred from names and where vanilla uses them (docs/WORLDMAP.md), not traced in code
    "stop": "the player stops here when walking past", "restart": "a lost life sends you back here",
    "merge": "where two paths join",
    "dokan": "the world's warp pipe: warps to any world reached, or World 1's pipe island (one per world)",
    "dokan2": "World 1's island pipe (the warp to the island lands on Fs01)",
    "focus": "camera", "fixX": "camera", "fixY": "camera", "fixZ": "camera", "scale": "camera",
    "noUI": "hides the course banner", "boarding": "W7 lift ride", "station": "W7 lift ride", "coaster": "W7 lift ride",
}

# ---- route types (table 0x60C88ED4) --------------------------------------------------------------------------------------
ROUTE_TYPES = {
    "道": "road", "砂": "sand", "木": "wood", "ジャンプ": "jump", "はしご": "ladder", "ツタ": "vine", "坂": "slope",
    "氷坂": "ice slope", "スイッチブロック": "switch block", "流砂": "quicksand", "雪": "snow", "氷": "ice", "雲": "cloud",
    "水": "water", "はしご右": "ladder right", "はしご左": "ladder left", "はしご岩": "rock ladder",
    "はしご岩右": "rock ladder right", "はしご岩左": "rock ladder left", "はしご縄": "rope ladder", "土": "dirt",
    "石橋": "stone bridge", "石": "stone",
}

CLASS_NAMES = {0: "free point", 1: "bend", 2: "entry", 3: "stop", 4: "house/other"}

# ---- the twelve archives the game loads (course-select cache 0x6016A300) ------------------------------------------------
SLOTS = ("W1", "W2a", "W3", "W4", "W5a", "W5b", "W6a", "W7", "W8a", "W8b", "W9a", "W9b")

# ---- names the game's code looks up without checking the result --------------------------------------------------------
# A map that lacks one of these crashes the game (null pointer read) when that code runs. Found in Ryujinx crash logs of
# a custom W1 (PC main+0x1E7FB4, the map music asking for W1S0) and by reading every caller of the point lookup 0x601E03D4.
REQUIRED_POINTS = {
    "W1": {"W1S0": "the map music looks it up (0x601E7F30)"},
    "W2a": {"W2S0": "the map music looks it up (0x601E7F30)"},
    "W4": {"W4G0": "the ghost-house effect looks it up (0x60222DC8)"},
    "W6a": {"W6C0": "the return to the W6 castle looks it up (0x601E5468)"},
    "W8b": {"F001": "the Bowser castle intro looks it up (0x601CEAA8)"},
}
# Map enemies: each one starts on the point carrying its flag (search 0x6021F6E0, table 0x60AFD488). With no such point
# the start index stays -1 and the enemy's position is read through a null pointer (0x601B0DB0).
ENEMY_FLAGS = {
    "W1": ("W1Item", "W1PakkunA"),
    "W2a": ("W2EnemyA", "W2EnemyB", "W2EnemyC"),
    "W3": ("W3EnemyA", "W3EnemyB"),
    "W4": ("W4EnemyA", "W4EnemyB", "W4EnemyC", "W4EnemyD"),
    "W5b": ("W5bEnemy1", "W5bEnemy2", "W5bEnemy3"),
    "W7": ("ChorobonA", "ChorobonB", "ChorobonC"),
}
# Map Yoshis: the companion manager (0x60C8AEF0) puts each map Yoshi on the point carrying its flag (0x601B5FD8, flag
# from 0x601C38F0). With no such point the Yoshi keeps the player's own point, and when the player next walks onto it
# the "Yoshi joins" event (sequence 116, or 115 later; requested by 0x601D2928) waits forever at step 324: the map is
# frozen. Seen with a GDB stub on Ryujinx (custom W1, 2026-10-27); vanilla has one Yoshi spot on each of these maps.
COMPANION_FLAGS = {"W1": ("balloon1",), "W3": ("balloon2",), "W4": ("bubble1",), "W6a": ("bubble2",)}

# Bones that place the world in the shared 3D space (0x6019F2F0 / 0x601A02F8): every map keeps them. W3 is the anchor;
# connectW<k> joins the two worlds of each link, k being the higher one (same table as wmtool's WORLD_LINKS).
LINK_BONE_PREFIXES = ("connectW", "GrandCentral")
WORLD_LINKS = [(3, 9), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (3, 2), (2, 1)]


def connect_bones(world_number):
    return {"connectW%d" % max(a, b) for a, b in WORLD_LINKS if world_number in (a, b)}


def slot_of(world_number, variant=""):
    """'W2a' for (2, 'a'); a world without that exact archive gets its first variant (W2 -> W2a)."""
    want = "W%d%s" % (world_number, variant or "")
    if want in SLOTS:
        return want
    return next((s for s in SLOTS if s.startswith("W%d" % world_number)), want)


def required_names(slot):
    """(points the slot must have {name: why}, flags some point must carry {flag: why})."""
    flags = {f: "%s's map enemy starts on the point flagged %s (0x6021F6E0); without one the game crashes" % (slot, f)
             for f in ENEMY_FLAGS.get(slot, ())}
    flags.update({f: "%s's map Yoshi waits on the point flagged %s (0x601B5FD8); without one it sits on the player's "
                     "point and the map freezes when the player walks back there (event 116)" % (slot, f)
                  for f in COMPANION_FLAGS.get(slot, ())})
    return REQUIRED_POINTS.get(slot, {}), flags


def point_class(name):
    """0x6019A214: 0 free point, 1 route bend, 2 entry, 3 stop, 4 Toad house / other."""
    if name[:1] == "F":
        return 0
    if name[:1] == "K":
        return 1
    if name[:1] == "W" and name[2:3] == "M":
        return 2
    if name[:1] != "W" or name[2:3] in ("K", "W"):
        return 4
    return 3


def course_index(name):
    """0x6040D864: point name -> 0-based course number in its world (-1: none). Level file: Course/<world>-<index+1>.sarc."""
    if len(name) != 4 or name[0] != "W":
        return -1
    c = name[3]
    n = int(c, 16) if c in "0123456789abcdef" else 0
    kind = name[2]
    if kind == "0":
        return n - 1
    if kind == "S":
        w = ord(name[1]) - ord("1")
        return 12 + w if 0 <= w < 5 else 12
    return {"A": 36, "B": n + 41, "C": n + 22, "G": 19, "K": n + 38 if n > 7 else n + 23,
            "M": 37, "T": n + 20, "W": 34, "X": 57}.get(kind, -1)



# ---- clear slots (CourseSlot_getIndex 0x6040E5B0; nsmbud-reverse F0301, F0332) -------------------------------------------
# The save keeps a clear flag only for the courses listed here (123 entries for normal exits, 82 for secret exits). A
# course with no slot is never recorded as cleared: the paths its exit opens stay shut (World 1's W106, F0301). Course
# numbers are course_index(); 37 (the start, W<n>M0) always counts as cleared. World number -> (normal, secret) courses.
COURSE_SLOTS = {
    1: ((0, 1, 2, 3, 4, 12, 20, 22, 24, 25, 26, 31, 32, 57),
        (0, 1, 2, 3, 4, 12, 20, 22)),
    2: ((0, 1, 2, 3, 4, 5, 13, 20, 22, 24, 25, 26, 31, 32),
        (0, 1, 2, 3, 4, 5, 13, 20, 22)),
    3: ((0, 1, 2, 3, 4, 14, 19, 20, 22, 24, 25, 26, 31, 32),
        (0, 1, 2, 3, 4, 14, 19, 20, 22)),
    4: ((0, 1, 2, 3, 4, 15, 19, 20, 22, 24, 25, 26, 31, 32, 33, 54),
        (0, 1, 2, 3, 4, 15, 19, 20, 22)),
    5: ((0, 1, 2, 3, 4, 5, 6, 16, 19, 20, 22, 24, 25, 26, 31, 32, 33, 36),
        (0, 1, 2, 3, 4, 5, 6, 16, 19, 20, 22, 36)),
    6: ((0, 1, 2, 3, 4, 5, 6, 20, 21, 22, 24, 25, 26),
        (0, 1, 2, 3, 4, 5, 6, 20, 21, 22)),
    7: ((0, 1, 2, 3, 4, 5, 19, 20, 22, 24, 25, 26, 31, 32, 36),
        (0, 1, 2, 3, 4, 5, 19, 20, 22, 36)),
    8: ((0, 1, 2, 3, 41, 42),
        (0, 1, 2, 3, 41, 42)),
    9: ((0, 1, 2, 3, 4, 5, 6, 7, 8, 24, 25, 26, 27),
        (0, 1, 2, 3, 4, 5, 6, 7, 8)),
}


# The framework's slot hook (nsmbud-reverse source/code/framework/course_slots.cpp, hooking CourseSlot_getIndex
# 0x6040E5B0; F0337): a hidden point Fq<course, 2 hex digits> marks a course the map uses that the save does not track.
# Each marked course borrows another course's entry, planned once for all worlds:
#  1. a course its world tracks but no point of its maps uses, in SLOT_HOOK_ORDER (S = 11 + world);
#  2. the same order, now courses whose points open nothing on that exit (never the start or a castle);
#  3. unused entries (rule 1) of the other worlds, World 9 down to World 1.
# A point Fr<course> lets a course a point uses lend its entry anyway (placeholders the player never reaches: a hidden
# W1S0, map enemies on hidden points); enemy courses lend only that way. Toad houses never lend: the game erases house
# entries on its own (reviveTargetKinokoHouse 0x603F75FC). A lending course has no entry for that exit (counted once).
SLOT_HOOK_ORDER = (57, "S", 19, 20, 21, 22, 36, 41, 42) + tuple(range(12)) + (31, 32, 33, 54, 55, 56)


def slot_marker(course):
    """The hidden point that asks the slot hook to keep a clear for this course: Fq05 for course 5 (W106)."""
    return "Fq%02x" % int(course)


def lend_marker(course):
    """The hidden point that lets the slot hook lend this course's entry although a point uses it: Fr0c for W1S0."""
    return "Fr%02x" % int(course)


def marked_courses(names, kind="q"):
    out = set()
    for n in names:
        if len(n) == 4 and n[:2] == "F" + kind:
            try:
                c = int(n[2:], 16)
            except ValueError:
                continue
            if c < 58:
                out.add(c)
    return out


def _is_house(c):
    return 23 <= c <= 30 or 46 <= c <= 53


def _is_enemy(c):
    return 31 <= c <= 33 or 54 <= c <= 56


def _tracked(world_number, course, secret):
    slots = COURSE_SLOTS.get(world_number)
    return bool(slots) and course in slots[1 if secret else 0]


class _SlotWorld:
    """What the slot hook reads of one world's maps (course_slots.cpp readWorld)."""

    def __init__(self, world_number, names, opens_normal=None, opens_secret=None):
        self.world = world_number
        self.used = {course_index(n) for n in names} - {-1}
        self.marked = marked_courses(names)
        self.allowed = marked_courses(names, "r")
        self.opens = (self.used if opens_normal is None else set(opens_normal),
                      self.used if opens_secret is None else set(opens_secret))
        self.lent = (set(), set())

    def lendable(self, b, u, pass_):
        if b < 0 or b >= 58 or _is_house(b) or b == 37 or b in self.marked or b in self.lent[u]:
            return False
        if b in self.allowed:
            return pass_ == 1
        if _is_enemy(b):
            return False
        if pass_ == 1:
            return b not in self.used
        return b in self.used and b not in self.opens[u] and b not in (22, 23, 41, 42)

    def order(self):
        return [11 + self.world if b == "S" else b for b in SLOT_HOOK_ORDER]


def _plan_own(W, plan):
    for u, secret in ((0, False), (1, True)):
        for c in sorted(W.marked):
            if _tracked(W.world, c, secret):
                continue
            plan[(W.world, c, secret)] = None
            for pass_ in (1, 2):
                b = next((b for b in W.order() if W.lendable(b, u, pass_) and _tracked(W.world, b, secret)), None)
                if b is not None:
                    plan[(W.world, c, secret)] = (W.world, b)
                    W.lent[u].add(b)
                    break


def global_slot_plan(worlds):
    """{(world, course, secret): (world, course) whose entry it uses, or None} for every marked course of every world.
    worlds: {world number: (point names of both variants, courses opening something on the normal exit, ... on the
    secret exit)} for the worlds whose maps the game loads (all nine in a full port; vanilla worlds count too)."""
    W = {w: _SlotWorld(w, *v) for w, v in worlds.items()}
    plan = {}
    for w in sorted(W):
        _plan_own(W[w], plan)
    for (w, c, secret), v in sorted(plan.items()):
        if v is not None:
            continue
        u = 1 if secret else 0
        for d in sorted(W, reverse=True):
            if d == w:
                continue
            b = next((b for b in W[d].order() if W[d].lendable(b, u, 1) and _tracked(d, b, secret)), None)
            if b is not None:
                plan[(w, c, secret)] = (d, b)
                W[d].lent[u].add(b)
                break
    return plan


def slot_plan(world_number, names, secret_used=None, normal_used=None):
    """{(course, secret): course of the same world whose entry it keeps its clear in} for the courses marked by Fq
    points in names (the map's point names, both variants of the world when it has two): rules 1-2 of the hook. A
    marked course its world cannot hold maps to None: at run time the hook then borrows from another world if one
    leaves an entry unused (global_slot_plan). secret_used / normal_used: courses whose points open something on that
    exit (None: every course a point uses)."""
    W = _SlotWorld(world_number, names, normal_used, secret_used)
    if not W.marked:
        return {}
    plan = {}
    _plan_own(W, plan)
    return {(c, sc): (v[1] if v else None) for (_w, c, sc), v in plan.items()}


def opens_of(point_rows):
    """(courses whose points open something on the normal exit, ... on the secret exit): columns 3/4 and 6/7."""
    n = {course_index(r[1]) for r in point_rows if len(r) > 4 and (r[3] or r[4])} - {-1}
    s_ = {course_index(r[1]) for r in point_rows if len(r) > 7 and (r[6] or r[7])} - {-1}
    return n, s_


def secret_used_of(point_rows):
    """The courses whose points open nodes or paths on their secret exit (point table columns 6 and 7)."""
    return opens_of(point_rows)[1]


def slot_room(world_number, names, secret=False):
    """How many more untracked courses the slot hook could still give a clear to in this map (rule 1)."""
    W = _SlotWorld(world_number, names)
    plan = {}
    _plan_own(W, plan)
    u = 1 if secret else 0
    return sum(1 for b in set(W.order()) if W.lendable(b, u, 1) and _tracked(world_number, b, secret))


def untracked_levels(world_number, names):
    """Courses of these point names whose normal exit the save keeps no clear for (they need the slot hook)."""
    out = set()
    for n in names:
        c = course_index(n)
        if n[:1] == "W" and c >= 0 and c != 37 and not _tracked(world_number, c, False):
            out.add(c)
    return sorted(out)


def has_slot(world_number, course, secret=False, names=None):
    """Does the save keep this course's (secret) clear? names: the map's point names, so Fq markers (the framework
    slot hook) count."""
    if course == 37:
        return True
    if names is not None:
        plan = slot_plan(world_number, names)
        if plan.get((course, secret)) is not None:
            return True
        if course in {v for (c, s_), v in plan.items() if s_ == secret}:
            return False                                 # lent out to a marked course
    return _tracked(world_number, course, secret)


def tracked_level_names(world_number):
    """The level point names W<n>0<k> whose course has a clear slot in this world."""
    return ["W%d0%x" % (world_number, c + 1) for c in COURSE_SLOTS.get(world_number, ((), ()))[0] if c < 12]

def level_tilesets(sarc_path):
    """{area file: [tileset names]} of a level archive: block 1 of each course/courseN.bin holds four 32-byte names
    (Pa0_jyotyu, Pa1_1-1_1...), each a romfs/Unit/<name>.szs the level loads."""
    import struct
    from . import archive
    d = Path(sarc_path).read_bytes()
    if d[:4] == b"Yaz0":
        d = archive.yaz0_decompress(d)
    ents = archive.parse_sarc(d)
    ents = ents[0] if isinstance(ents, tuple) else ents
    out = {}
    for name, b in ents:
        if not re.search(r"course\d\.bin$", name) or len(b) < 8:
            continue
        off, size = struct.unpack_from("<II", b, 0)
        if size >= 128 and off + 128 <= len(b):
            out[name] = [t for t in (b[off + 32 * i:off + 32 * i + 32].split(b"\0")[0].decode("ascii", "replace")
                                     for i in range(4)) if t]
    return out


# Goal poles (Yaz0moto's spritedata.xml for NSMBU): Goal Pole 31 (nybble 5: regular / secret exit), Painted Goal Pole
# 503, NSLU goal poles 630 / 631 (nybble 5: secret exit). Sprites are block 8 of each course<N>.bin, 24 bytes each
# (type u16, x u16, y u16, events u16, settings1 u32, settings2 u32, zone, layer, ...); nybble 5 is the top nybble of
# settings1. Vanilla 1-2 has both (its secret goal opens RW102W1S0), 1-1 only a regular one.
GOAL_SPRITES = (31, 503, 630, 631)


def level_goals(sarc_path):
    """{"normal": n, "secret": n}: the goal poles of a level archive, over all its areas."""
    import struct
    from . import archive
    d = Path(sarc_path).read_bytes()
    if d[:4] == b"Yaz0":
        d = archive.yaz0_decompress(d)
    ents = archive.parse_sarc(d)
    ents = ents[0] if isinstance(ents, tuple) else ents
    out = {"normal": 0, "secret": 0}
    for name, b in ents:
        if not re.search(r"course\d\.bin$", name) or len(b) < 64:
            continue
        off, size = struct.unpack_from("<II", b, 7 * 8)
        for i in range(off, min(off + size, len(b)) - 23, 24):
            t = struct.unpack_from("<H", b, i)[0]
            if t in GOAL_SPRITES:
                secret = (struct.unpack_from("<I", b, i + 8)[0] >> 28) & 0xF
                out["secret" if secret else "normal"] += 1
    return out


def point_label(name):
    """A point's name with the level archive it opens: 'W106 · 1-6' (Course/1-6.sarc), or just the name."""
    lf = level_file(name)
    return "%s · %s" % (name, lf[len("Course/"):-len(".sarc")]) if lf else name


def level_file(name):
    """'Course/1-21.sarc' for W1T0, or None for points without a level."""
    if point_class(name) not in (3, 4):
        return None
    ci = course_index(name)
    if ci < 0 or len(name) < 2 or not name[1].isdigit():
        return None
    return "Course/%s-%d.sarc" % (name[1], ci + 1)


def split(cell):
    return [t for t in (cell or "").split(",") if t]


def paths(cell):
    """An unlock-path column: the game keeps 9 characters per entry."""
    return [t[:9] for t in split(cell)]


def route_ends(rid):
    return (rid[1:5], rid[5:9]) if len(rid) == 9 and rid[0] == "R" else None


# ---- paths and the play-through ----------------------------------------------------------------------------------------
def segment_graph(point_rows, route_rows):
    adj = defaultdict(list)
    for r in route_rows:
        ends = route_ends(r[0]) if r else None
        if ends:
            adj[ends[0]].append((ends[1], r[0]))
            adj[ends[1]].append((ends[0], r[0]))
    pipe = {f: r[1] for r in point_rows if len(r) > 2 for f in split(r[2]) if f in ("dokan", "dokan2")}
    if "dokan" in pipe and "dokan2" in pipe:
        a, b = pipe["dokan"], pipe["dokan2"]
        adj[a].append((b, "(pipe)"))
        adj[b].append((a, "(pipe)"))
    names = {r[1] for r in point_rows if len(r) > 1}
    if "dokan" in pipe and "Fs01" in names:                  # the warp to World 1's island lands on Fs01 (F0318)
        adj[pipe["dokan"]].append(("Fs01", "(warp pipe)"))
        adj["Fs01"].append((pipe["dokan"], "(warp pipe)"))
    return adj


def path_segments(path, adj):
    """The segments an unlock path opens: its walk from one W point to the other through F and K points, or None."""
    ends = route_ends(path)
    if not ends:
        return None
    a, b = ends
    prev = {a: None}
    queue = deque([a])
    while queue:
        p = queue.popleft()
        if p == b:
            break
        if p != a and p[:1] == "W":
            continue
        for q, rid in adj[p]:
            if q not in prev:
                prev[q] = (p, rid)
                queue.append(q)
    if b not in prev:
        return None
    segs, p = [], b
    while prev[p]:
        p, rid = prev[p]
        segs.append(rid)
    return list(reversed(segs))


# ---- path bones (0x60181FC0) ---------------------------------------------------------------------------------------------
# The walkable paths are not in the CSVs: they are bones of the map-point model. A bone named R<from><to> (9 characters,
# digits at [4] and [8]) starts a path record; the F and K bones right after it, in bone order, are its walk (0x60182C0C).
# Every vanilla path has one, also those open from the start (RW1M0W101) and direct ones (RW101W102, empty walk). A map
# without them loads but the player cannot move (first Starter Island test with path bones missing).
MAX_PATHS = 128             # path records per map (828 bytes each from +288812)
MAX_PATH_WALK = 40          # walk points per path


def path_bone_ok(path):
    return len(path) == 9 and path[0] == "R" and path[4].isdigit() and path[8].isdigit()


def _walk_points(a, segs):
    """The points a list of segments passes through, from a, without the two ends."""
    pts, p = [], a
    for rid in segs:
        x, y = route_ends(rid)
        p = y if x == p else x
        pts.append(p)
    return pts[:-1]


def map_paths(point_rows, route_rows):
    """[(path id, [walk points in order])]: the path bones a map needs. The unlock paths of the point table come first;
    segments on no unlock path are joined into paths between stops (W points, path ends and F points where routes
    meet), like vanilla's RW1M0W101. Paths that end on another map are left out (their walk is not on this map)."""
    adj = defaultdict(list)
    real = [r[0] for r in route_rows if r and route_ends(r[0])]
    for rid in real:
        x, y = route_ends(rid)
        adj[x].append((y, rid))
        adj[y].append((x, rid))
    out, covered, seen = [], set(), set()
    for r in point_rows:
        for col in (4, 7):
            for path in (paths(r[col]) if len(r) > col else []):
                if path in seen:
                    continue
                seen.add(path)
                segs = path_segments(path, adj)
                if segs:
                    out.append((path, _walk_points(path[1:5], segs)))
                    covered.update(segs)
    ends = {e for p, _ in out for e in route_ends(p)}
    free = defaultdict(list)
    for rid in real:
        if rid not in covered:
            x, y = route_ends(rid)
            free[x].append((y, rid))
            free[y].append((x, rid))

    def stop(p):
        return p[:1] == "W" or p in ends or len(adj[p]) != 2
    done = set()

    def walk_from(a, q, rid):
        walk, nxt = [], q
        done.add(rid)
        while not stop(nxt) and nxt != a:
            cont = [(n, s) for n, s in free[nxt] if s not in done]
            if not cont:
                break
            walk.append(nxt)
            nxt, step = cont[0]
            done.add(step)
        if route_ends(rid)[0] == a:                                  # the way the first segment is written
            out.append(("R%s%s" % (a, nxt), walk))
        else:
            out.append(("R%s%s" % (nxt, a), walk[::-1]))
    for a in sorted(p for p in free if stop(p)):
        for q, rid in free[a]:
            if rid not in done:
                walk_from(a, q, rid)
    for a in sorted(free):                                           # loops with no stop on them
        for q, rid in free[a]:
            if rid not in done:
                walk_from(a, q, rid)
    return out


# ---- one neighbour per direction (0x60178558) ------------------------------------------------------------------------
# Each route links its two points by direction, worked out from their positions (calcConnect_ 0x601A3C64): the up/down
# pair (slots 0/1, by height) only when both the x and the z offset are under 3 units, i.e. the route is vertical;
# otherwise north/south when |dx| < 3 or |dx| < |dz|, else east/west. A point keeps one neighbour per direction, so a second route leaving it the same way replaces the first
# (in test 4 the castle's link to the tower was lost to the walk to World 2). No vanilla map has such a clash.
def heading(a, b):
    """Compass direction of b seen from a (positions x, y, z), or None for the up/down pair."""
    dx, dz = b[0] - a[0], b[2] - a[2]
    if abs(dx) < 3 and abs(dz) < 3:
        return None
    if abs(dx) < 3 or (abs(dz) >= 3 and abs(dx) < abs(dz)):
        return "south" if dz > 0 else "north"
    return "east" if dx > 0 else "west"


def heading_clashes(route_ids, positions):
    """{(point, direction): [routes]} for points with two routes leaving the same way. positions: {name: (x, y, z)}."""
    use = defaultdict(list)
    for rid in route_ids:
        ends = route_ends(rid)
        if not ends or ends[0] not in positions or ends[1] not in positions:
            continue
        a, b = ends
        for p, q in ((a, b), (b, a)):
            h = heading(positions[p], positions[q])
            if h:
                use[(p, h)].append(rid)
    return {k: v for k, v in use.items() if len(v) > 1}


def slot_losses(route_ids, positions):
    """[(point, slot, lost neighbour, new neighbour, route)]: links the game's slot table overwrites, built exactly like
    calcConnect_ (0x601A3C64) does, route by route: |dx| < 3 and |dz| < 3 -> slots 0/1 (up/down) by height; else
    north/south (2/3) when |dx| < 3 or |dx| < |dz|, else east/west (4/5). Two routes leaving a point the same way: only
    the last one stays."""
    slots, lost = {}, []
    for rid in route_ids:
        ends = route_ends(rid)
        if not ends or ends[0] not in positions or ends[1] not in positions:
            continue
        a, b = ends
        pa, pb = positions[a], positions[b]
        dx, dz = pa[0] - pb[0], pa[2] - pb[2]
        if abs(dx) >= 3 or abs(dz) >= 3:
            if abs(dx) < 3 or (abs(dz) >= 3 and abs(dx) < abs(dz)):
                sets = ((a, 3, b), (b, 2, a)) if dz > 0 else ((a, 2, b), (b, 3, a))
            else:
                sets = ((a, 4, b), (b, 5, a)) if dx > 0 else ((b, 4, a), (a, 5, b))
        else:
            sets = ((b, 1, a), (a, 0, b)) if pa[1] - pb[1] <= 0 else ((a, 1, b), (b, 0, a))
        for p, s, q in sets:
            row = slots.setdefault(p, [None] * 6)
            if row[s] is not None and row[s] != q:
                lost.append((p, s, row[s], q, rid))
            row[s] = q
    return lost


CLIMB_TYPES = ("はしご", "はしご左", "はしご右", "はしご岩", "はしご岩左", "はしご岩右", "はしご縄", "ツタ")
AMBUSH_FLAGS = ("W1PakkunA", "W1PakkunB", "W1Item", "W2EnemyA", "W2EnemyB", "W2EnemyC", "W3EnemyA", "W3EnemyB",
                "W4EnemyA", "W4EnemyB", "W4EnemyC", "W4EnemyD", "W5bEnemy1", "W5bEnemy2", "W5bEnemy3", "W5bEnemy4",
                "W5bEnemy5", "W5bEnemy6", "W5bEnemy7", "ChorobonA", "ChorobonB", "ChorobonC")


def is_vertical(a, b):
    """The game files a route under up/down (slots 0/1) only when both ends share x and z within 3 units."""
    return abs(a[0] - b[0]) < 3 and abs(a[2] - b[2]) < 3


def climb_problems(route_rows, positions, flags_of=None, all_routes=None):
    """[(route, text)] for ladders and vines that won't play as climbs in the game.
    Climbs are vertical in every vanilla map (both ends at the same x and z): calcConnect_ (0x601A3C64) gives such a
    route the up/down slots and initMove_ (0x601D59EC) applies the climb speed only there. A climb whose far end holds a
    map enemy is replaced by the enemy's ambush (Mario jumps at it, as in a W1 test with the Piranha Plant on Fb01)."""
    out = []
    flags_of = flags_of or {}
    degree = defaultdict(int)
    for r in (all_routes if all_routes is not None else route_rows):
        for e in (route_ends(r[0]) if r else None) or ():
            degree[e] += 1
    for r in route_rows:
        if len(r) < 2 or r[1] not in CLIMB_TYPES:
            continue
        ends = route_ends(r[0])
        if not ends:
            continue
        a, b = ends
        # The stick only gives compass directions (getDirFromKeyMask_ 0x601D8424), never up/down: a climb can only be
        # walked onto by passing through a plain point with exactly two roads, as every vanilla ladder is.
        for e in ends:
            if e not in positions or any(f.startswith("link") for f in flags_of.get(e, ())):
                continue                                  # goes on in another map: its other roads are there
            if e[:1] == "W" or "stop" in flags_of.get(e, ()) or degree[e] != 2:
                out.append((r[0], "Route %s's end %s is a stop (a level, a stop flag, or not exactly two roads): Mario "
                            "can't choose a climb there, so it only works one way (fine if you want that). For two-way, "
                            "use Fix climb (adds a pass-through point between)." % (r[0], e)))
                break
        if a in positions and b in positions and not is_vertical(positions[a], positions[b]):
            pa, pb = positions[a], positions[b]
            out.append((r[0], "Route %s is a %s but not vertical (x differs by %.0f, z by %.0f): the game only climbs "
                        "straight up or down, both ends at the same x and z. Use Make vertical." % (
                            r[0], ROUTE_TYPES.get(r[1], r[1]), abs(pa[0] - pb[0]), abs(pa[2] - pb[2]))))
        elif a in positions and b in positions and abs(positions[a][1] - positions[b][1]) < 60:
            out.append((r[0], "Route %s is a vertical %s but rises only %.0f units: raise one end (vanilla ladders rise "
                        "120-600) so Mario has something to climb." % (
                            r[0], ROUTE_TYPES.get(r[1], r[1]), abs(positions[a][1] - positions[b][1]))))
        for e in ends:
            hit = [f for f in flags_of.get(e, ()) if f in AMBUSH_FLAGS]
            if hit:
                out.append((r[0], "Route %s climbs to %s, where the map enemy %s waits: its ambush replaces the climb. "
                            "End the climb on another point." % (r[0], e, hit[0])))
    return out


def heading_problems(route_ids, positions):
    return ["Routes %s leave %s in the same direction (%s): the game keeps one link per direction (0x60178558), so "
            "only the last one works. Move a point or add a bend (K) so they leave differently." % (
                " and ".join(v), p, h) for (p, h), v in sorted(heading_clashes(route_ids, positions).items())]


def paths_from_bones(bone_names):
    """[(path id, walk)] the way the game reads them from the bones, in bone order (0x60181FC0 / 0x60182C0C)."""
    out = []
    for i, n in enumerate(bone_names):
        if path_bone_ok(n):
            walk = []
            for m in bone_names[i + 1:]:
                if m[:1] not in ("K", "F") or len(walk) == MAX_PATH_WALK:
                    break
                walk.append(m)
            out.append((n, walk))
    return out[:MAX_PATHS]


def unwalkable(route_rows, path_list):
    """Segments that lie on no path: the player cannot walk them."""
    steps = set()
    for path, walk in path_list:
        seq = [path[1:5]] + list(walk) + [path[5:9]]
        steps.update(frozenset(s) for s in zip(seq, seq[1:]))
    return [r[0] for r in route_rows if r and route_ends(r[0]) and frozenset(route_ends(r[0])) not in steps]


def path_problems(path_list, bones=None):
    """Errors that keep the game from registering the paths of path_list (see map_paths)."""
    errs, owner = [], {}
    if len(path_list) > MAX_PATHS:
        errs.append("%d paths; the game keeps %d per map." % (len(path_list), MAX_PATHS))
    for path, walk in path_list:
        if not path_bone_ok(path):
            errs.append("Path %s: the game only registers a path whose two end names end in a digit (0x60181FC0); "
                        "rename %s." % (path, " and ".join(e for e in route_ends(path) if not e[-1:].isdigit()) or "its ends"))
        if len(walk) > MAX_PATH_WALK:
            errs.append("Path %s walks through %d points; the game keeps %d. Put a stop (F point) in the middle."
                        % (path, len(walk), MAX_PATH_WALK))
        for p in walk:
            if p in owner:
                errs.append("%s is on two paths (%s and %s); a point can be on one path's walk only. Make it a stop "
                            "(F point) where the paths meet, or split the route." % (p, owner[p], path))
            owner.setdefault(p, path)
    return errs


def simulate(point_rows, route_rows):
    """Every course cleared by both exits: (start, points reached, segments walked, other-map points reached)."""
    names = {r[1] for r in point_rows if len(r) > 1}
    by_name = {r[1]: r for r in point_rows if len(r) > 1}
    adj = segment_graph(point_rows, route_rows)
    chains = {}
    for r in point_rows:
        if len(r) > 7:
            for path in paths(r[4]) + paths(r[7]):
                chains[path] = path_segments(path, adj) or []
    gated = {s for segs in chains.values() for s in segs}
    start = next((n for n in names if point_class(n) == 2), None)
    if not start:
        return None, set(), set(), set()
    opened = {r[0] for r in route_rows if r and r[0] not in gated}
    opened.add("(pipe)")
    seen, used, external = {start}, set(), set()
    changed = True
    while changed:
        changed = False
        queue = deque(seen)
        while queue:
            p = queue.popleft()
            row = by_name.get(p)
            if row and len(row) > 7:
                for path in paths(row[4]) + paths(row[7]):
                    for s in chains.get(path, []):
                        if s not in opened:
                            opened.add(s)
                            changed = True
            for q, rid in adj[p]:
                if rid in opened:
                    used.add(rid)
                    if q not in seen:
                        seen.add(q)
                        queue.append(q)
                        changed = True
                        if q not in names and q[:1] != "K":
                            external.add(q)
    used.discard("(pipe)")
    return start, seen, used, external


class Report:
    def __init__(self):
        self.errors, self.warnings, self.summary = [], [], []

    @property
    def ok(self):
        return not self.errors


def check(point_rows, route_rows, point_bytes, route_bytes, bones, world_number, variant="", course_dir=None, hidden=(),
          bone_order=None, positions=None, partner_names=None):
    """All of wmtool's checks. bones: set of bone names in the map-point model (or None to skip bone checks).
    hidden: placeholder points that are meant to be out of reach (left out of the play-through warnings).
    bone_order: the model's bone names in order, for the path checks (the game reads paths from bone order).
    positions: {point or bend: (x, y, z)}, for the one-neighbour-per-direction check."""
    rep = Report()
    E, W = rep.errors.append, rep.warnings.append
    if point_bytes > MAX_POINT_CSV:
        E("The point table is %d bytes; the game reads at most %d." % (point_bytes, MAX_POINT_CSV))
    if route_bytes > MAX_ROUTE_CSV:
        E("The route table is %d bytes; the game reads at most %d." % (route_bytes, MAX_ROUTE_CSV))
    if len(point_rows) > MAX_POINTS:
        E("%d points; the game keeps %d." % (len(point_rows), MAX_POINTS))
    if len(route_rows) > MAX_ROUTES:
        E("%d routes; the game keeps %d." % (len(route_rows), MAX_ROUTES))
    names = [r[1] for r in point_rows if len(r) > 1]
    _opn, _ops = opens_of(point_rows)
    plan = slot_plan(world_number, names + list(partner_names or ()), _ops, _opn)
    if plan:
        slot_note = ("Framework slot hook (course_slots.cpp): " + ", ".join(
            "course %d%s keeps its clear in course %s's entry" % (c, " secret" if sc else "", "-" if v is None else v)
            for (c, sc), v in sorted(plan.items())) + ". Needs the Koopatlas DX framework build.")
    route_ids = [r[0] for r in route_rows if r]
    adj = segment_graph(point_rows, route_rows)
    from .pointactions import problems as point_action_problems
    for m in point_action_problems(point_rows, "W%d%s" % (world_number, variant or "")):
        W(m)
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        E("Duplicate point names: " + ", ".join(dups))
    for rid in sorted({x for x in route_ids if route_ids.count(x) > 1}):
        W("Route %s is listed twice; the game keeps the first." % rid)
    f_points = [n for n in names if n[:1] == "F"]
    if len(f_points) > MAX_SAVED_F_POINTS:
        E("%d F points; the save keeps state for %d." % (len(f_points), MAX_SAVED_F_POINTS))
    if not any(point_class(n) == 2 for n in names):
        (W if variant else E)("No entry point (W%dM0): the map has nowhere to start%s." % (
            world_number, " unless it is entered from another map" if variant else ""))
    slot = slot_of(world_number, variant)
    need_points, need_flags = required_names(slot)
    for n, why in need_points.items():
        if n not in names:
            E("No point %s: %s, so the game crashes without it. Add it (it may be out of reach)." % (n, why))
    carried = {f for r in point_rows if len(r) > 2 for f in split(r[2])}
    for f, why in need_flags.items():
        if f not in carried:
            E("No point has the flag %s: %s." % (f, why))
    courses = {}
    for i, r in enumerate(point_rows):
        name = r[1] if len(r) > 1 else "?"
        where = "Point %s (row %d)" % (name, i)
        if len(r) != 9:
            E("%s: %d columns, expected 9." % (where, len(r)))
            continue
        if str(i) != r[0]:
            W("%s: index column is %r." % (where, r[0]))
        if len(name) != 4:
            E("%s: names are 4 characters." % where)
        if name[:1] not in "WFK":
            W("%s: unknown prefix %r (W stop, F free point, K bend)." % (where, name[:1]))
        for flag in split(r[2]):
            if flag not in KNOWN_FLAGS and not flag.startswith("lookW"):
                W("%s: flag %r is not a name the game knows (it is ignored)." % (where, flag))
        for col, limit in COLUMN_LIMITS.items():
            if len(split(r[col])) > limit:
                E("%s: %d entries in '%s'; the game keeps %d (the extra ones overwrite the first)." % (
                    where, len(split(r[col])), COLUMNS[col], limit))
        for col in (4, 7):
            for entry in split(r[col]):
                if len(entry) > 9:
                    W("%s: %r is longer than 9 characters; the game reads %r." % (where, entry, entry[:9]))
            for path in paths(r[col]):
                ends = route_ends(path)
                if not ends:
                    E("%s: path %r is not R + point + point." % (where, path))
                elif path_segments(path, adj) is None:
                    local = all(e in names or e[:1] == "K" for e in ends)
                    (E if local else W)("%s: path %s has no chain of segments in the route table%s." % (
                        where, path, "" if local else " (it ends on another map)"))
        for col in (3, 6):
            for n in split(r[col]):
                if n not in names:
                    W("%s: opens node %s, not a point of this map (another world's?)." % (where, n))
        if point_class(name) in (3, 4):
            ci = course_index(name)
            if ci >= MAX_COURSES:
                E("%s: course index %d is over the game's %d." % (where, ci, MAX_COURSES))
            if ci >= 0:
                if ci in courses:
                    W("%s: same level (%d-%d) as %s." % (where, world_number, ci + 1, courses[ci]))
                courses.setdefault(ci, name)
                dirs = course_dir if isinstance(course_dir, (list, tuple)) else [course_dir] if course_dir else []
                level = "%d-%d.sarc" % (world_number, ci + 1)
                found = next((Path(d) / level for d in dirs if (Path(d) / level).exists()), None)
                if found is not None:
                    try:
                        goals = level_goals(found)
                    except Exception:
                        goals = None
                    sec_paths = paths(r[7]) if len(r) > 7 else []
                    boss_end = name[2] in "CAB"           # castles, airships, Bowser: no goal pole (W6C0 6-23)
                    if goals is not None and sec_paths and not goals["secret"] and not boss_end:
                        W("%s: its secret exit opens %s, but level Course/%s has no secret goal, so it can never be "
                          "found. In Yaz0moto, set 'Secret Exit' (nybble 5) on a Goal Pole of the level." % (
                              where, ", ".join(sec_paths), level))
                    units = [Path(d).parent / "Unit" for d in dirs]
                    try:
                        sets = level_tilesets(found)
                    except Exception as e:
                        sets = {}
                        W("%s: level file %s cannot be read (%s)." % (where, found, e))
                    gone = sorted({t for ts in sets.values() for t in ts
                                   if not any((u / (t + ".szs")).exists() for u in units)})
                    if gone:
                        E("%s: level Course/%s uses tilesets %s, not in the mod's or the game's Unit folder: the "
                          "level cannot load. Add Unit/<name>.szs to the mod." % (where, level, ", ".join(gone)))
                if dirs and found is None:
                    if name[2] == "K":
                        W("%s: level file Course/%s does not exist (fine for a Toad house)." % (where, level))
                    else:
                        E("%s: level file Course/%s does not exist, in the mod or the game: entering %s crashes the "
                          "game (main+0x40D33C). Add a Course/%s to the mod or remove the point." % (where, level, name, level))
        ci = course_index(name)
        if ci >= 0 and world_number in COURSE_SLOTS:
            for col, secret in ((4, False), (7, True)):
                opened = paths(r[col]) if len(r) > col else []
                lend = plan.get((ci, secret), False)
                if opened and lend is None:
                    W("%s: World %d has no free entry of its own for course %d%s; the framework slot hook borrows an "
                      "entry another world leaves unused (checked for all worlds by the World Maker's Port Newer… / the "
                      "port report). If none is free, %s never open%s." % (
                          where, world_number, ci, " (secret exit)" if secret else "", ", ".join(opened),
                          "s" if len(opened) == 1 else ""))
                elif opened and lend is False and not has_slot(world_number, ci, secret, names):
                    if secret:
                        hint = "Secret exits are tracked for %s." % ", ".join(
                            n for n in names if course_index(n) >= 0 and has_slot(world_number, course_index(n), True, names))
                    else:
                        free = [n for n in tracked_level_names(world_number) if n not in names]
                        hint = ("World %d tracks levels %s; free: %s. Or turn on More levels per world (the framework "
                                "slot hook, hidden point %s)." % (
                                    world_number, ", ".join(tracked_level_names(world_number)), ", ".join(free) or "none",
                                    slot_marker(ci)))
                    E("%s: the game keeps no %s clear for course %d of World %d, so it never records that exit and %s "
                      "never open%s (CourseSlot_getIndex 0x6040E5B0). %s" % (
                          where, "secret-exit" if secret else "normal", ci, world_number, ", ".join(opened),
                          "s" if len(opened) == 1 else "", hint))
        if bones is not None and name not in bones:
            W("%s: no bone named %s in the map-point model (the point has no position)." % (where, name))
    for r in route_rows:
        if not r:
            continue
        rid = r[0]
        ends = route_ends(rid)
        if not ends:
            E("Route %r: route ids are R + 4-character point + 4-character point." % rid)
            continue
        if len(r) < 2 or r[1] not in ROUTE_TYPES:
            W("Route %s: type %r is not one of the %d the game knows." % (rid, r[1] if len(r) > 1 else "", len(ROUTE_TYPES)))
        for e in ends:
            if e not in names and e[:1] != "K":
                W("Route %s: end %s is not a point of this map (link to another map?)." % (rid, e))
            if e[:1] == "K" and bones is not None and e not in bones:
                W("Route %s: bend %s has no bone in the map-point model." % (rid, e))
    if positions is not None:
        for m in heading_problems(route_ids, positions):
            E(m)
        flags_of = {r[1]: split(r[2]) for r in point_rows if len(r) > 2}
        for _rid, m in climb_problems(route_rows, positions, flags_of, route_rows):
            W(m)
    if bone_order is not None:
        path_list = paths_from_bones(list(bone_order))
        registered = {p for p, _ in path_list}
        if route_ids and not path_list:
            (E if any(point_class(n) == 2 for n in names) else W)("The map-point model has no path bones (R<from><to>): the game builds no paths and the player cannot "
              "move (0x60181FC0).")
        for r in point_rows:
            for col in (4, 7):
                for path in (paths(r[col]) if len(r) > col else []):
                    if path not in registered and path_segments(path, adj) is not None:
                        E("Path %s (opened by %s) has no path bone, so the game cannot open it." % (path, r[1]))
        if path_list:
            lost = unwalkable(route_rows, path_list)
            if lost:
                W("Routes on no path bone (the player cannot walk them): " + ", ".join(lost[:12]) + (" …" if len(lost) > 12 else ""))
    if bones is not None and variant != "b":
        missing = connect_bones(world_number) - set(bones)
        if missing:
            W("No bone %s: this map cannot be lined up with its neighbours (0x601A02F8 then reads bone -1)." % (
                ", ".join(sorted(missing))))
    start, seen, used, external = simulate(point_rows, route_rows)
    if start:
        shown = [n for n in names if n not in hidden]
        unreachable = [n for n in shown if n not in seen]
        unused = [r[0] for r in route_rows if r and r[0] not in used]
        rep.summary.append("Play-through from %s: %d/%d points reachable, %d/%d routes walkable." % (
            start, len(seen & set(shown)), len(shown), len(used), len(route_rows)))
        if external:
            rep.summary.append("Leads to other maps: " + ", ".join(sorted(external)))
        if unreachable:
            W("Never reachable (no open route leads there): " + ", ".join(unreachable))
        if unused:
            W("Routes never walkable: " + ", ".join(unused))
    if plan:
        rep.summary.append(slot_note)
    return rep

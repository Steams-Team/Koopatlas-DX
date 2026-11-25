"""
kdx/unlocks.py - Koopatlas-style unlock expressions for paths: "1-2 secret OR 1-3".

In the game a path R<from><to> opens when ANY point whose exit lists it is cleared (RouteMgr::checkMovable 0x6022AE5C:
the walk is allowed when one of the openers is cleared, F0279). So an expression is an OR of terms, each a level and
the exit that counts. The point table is the real data: a term is the path written in that point's "Opens paths"
(column 4 for the normal exit, 7 for the secret exit), with the path's far point in "Opens points" (3 / 6). "AND" has
no native equivalent (it needs framework code), so it is refused with that reason.

    expr = describe(point_rows, "RW102W103")              # "1-2"  (or "W102", "1-2 secret OR 1-3", "" = nobody)
    terms = parse("1-2 secret or 1-3", point_ids)         # [("W102", "secret"), ("W103", "normal")]
    rows = apply(point_rows, "RW102W103", terms)          # new point rows (copies)
"""
from __future__ import annotations

import re

from . import rules

COLS = {"normal": (3, 4), "secret": (6, 7)}


class UnlockError(ValueError):
    pass


def label(pid):
    lf = rules.level_file(pid)
    return lf[len("Course/"):-len(".sarc")] if lf else pid


def openers(point_rows, path):
    """[(point, 'normal'|'secret')] whose exit opens path."""
    out = []
    for r in point_rows:
        for ex, (_nc, pc) in COLS.items():
            if path in rules.paths(r[pc] if len(r) > pc else ""):
                out.append((r[1], ex))
    return out


def describe(point_rows, path):
    return " OR ".join(label(p) + (" secret" if ex == "secret" else "") for p, ex in openers(point_rows, path))


def parse(expr, point_ids):
    """Terms of an expression: levels as 1-2 / W102 / 1-21 (tower) ..., optionally followed by 'secret'; joined by
    OR, '|' or ','. Raises UnlockError."""
    expr = (expr or "").strip()
    if not expr:
        return []
    if re.search(r"\band\b|&", expr, re.I):
        raise UnlockError("The game opens a path when any one of its openers is beaten; it has no AND (RouteMgr::"
                          "checkMovable 0x6022AE5C). Use OR, or chain the paths (one opens the point that opens the next).")
    by_label = {label(p).lower(): p for p in point_ids}
    by_label.update({p.lower(): p for p in point_ids})
    terms = []
    for part in re.split(r"\s*(?:\bor\b|\||,)\s*", expr, flags=re.I):
        if not part:
            continue
        words = part.split()
        ex = "normal"
        if words and words[-1].lower() in ("secret", "s", "secret-exit"):
            ex, words = "secret", words[:-1]
        elif words and words[-1].lower() in ("normal", "n"):
            words = words[:-1]
        key = " ".join(words).lower()
        if key not in by_label:
            raise UnlockError("%r is not a point of this map (write a level as 1-2 or a point as W102)." % " ".join(words))
        t = (by_label[key], ex)
        if t not in terms:
            terms.append(t)
    return terms


def apply(point_rows, path, terms):
    """Point rows (copied) where exactly the terms' exits open path; the far point is added to / dropped from Opens
    points with it (dropped only when that exit opens no other path to it)."""
    ends = rules.route_ends(path)
    if not ends:
        raise UnlockError("%r is not a path name (R + point + point)." % path)
    dest = ends[1]
    want = set(terms)
    rows = [list(r) + [""] * (9 - len(r)) for r in point_rows]
    for r in rows:
        for ex, (nc, pc) in COLS.items():
            ps = rules.split(r[pc])
            ns = rules.split(r[nc])
            has = path in ps
            if (r[1], ex) in want and not has:
                ps.append(path)
                if dest not in ns:
                    ns.append(dest)
            elif (r[1], ex) not in want and has:
                ps.remove(path)
                if dest in ns and not any((rules.route_ends(q) or ("", ""))[1] == dest for q in ps):
                    ns.remove(dest)
            r[pc], r[nc] = ",".join(ps), ",".join(ns)
    return rows


def paths_through(world_paths, rid):
    """The unlock paths (from rules.map_paths: [(path, walk)]) whose chain uses the road rid."""
    a, b = rules.route_ends(rid)
    out = []
    for path, walk in world_paths:
        seq = [path[1:5]] + list(walk) + [path[5:9]]
        if any({x, y} == {a, b} for x, y in zip(seq, seq[1:])):
            out.append(path)
    return out

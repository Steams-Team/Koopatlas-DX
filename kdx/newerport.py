"""
kdx/newerport.py - Newer Super Mario Bros. Wii's world maps as Deluxe worlds, all at once.

Newer's maps (Koopatlas .kpmap + the mod's Maps/*.kpbin.LZ) hold 14 worlds on 13 maps; Deluxe loads twelve world
archives (rules.SLOTS). PLAN puts each Newer world in the Deluxe slot closest to its place in the game; maps that hold
several worlds (Fullmap: 6, MountainBackside: 2) are split by kdx.koopatlas.world_regions. The worlds Deluxe has no
archive for are listed in LEFT_OUT with why.

    results = port_all(kpmap_dir, game_dir, romfs_root, out_dir, mod_root=None)
"""
from __future__ import annotations

from pathlib import Path

# (Deluxe slot, Newer map, region (Newer level world) or None for the whole map, name)
PLAN = [
    ("W1", "YoshiIsland", None, "Yoshi's Island"),
    ("W2a", "Fullmap", 2, "Rubble Ruins"),
    ("W3", "Fullmap", 3, "Mushroom Peaks"),
    ("W4", "MountainBackside", 4, "Sakura Village"),
    ("W5a", "MountainBackside", 5, "Freezeflame Glacier"),
    ("W5b", "Flamesinside", None, "Freezeflame Volcano"),
    ("W6a", "Fullmap", 6, "Pumpkin Boneyard"),
    ("W7", "StarrySky", None, "Starry Skies"),
    ("W8a", "KoopaPlanet", None, "Koopa Planet"),
    ("W8b", "KoopaPlanetUnd", 8, "Koopa Core"),
    ("W9a", "Goldwood", None, "Goldwood Forest"),
    ("W9b", "Bonus", None, "Special World"),
]
# Newer maps or parts with no Deluxe archive left (the course-select cache 0x6016A300 loads exactly twelve).
LEFT_OUT = [
    ("Sewer", None, "Soggy Sewers", "Rubble Ruins' second map; Deluxe's World 2 has one archive (W2a)"),
    ("Fullmap", 7, "Sky Mountain (7-1 to 7-3)", "the first part of Starry Skies, on another map than its rest"),
    ("Fullmap", 10, "Bombard Cliffs", "secret world E"),
    ("Minimega", None, "Mini-Mega Island", "secret world B"),
    ("CrystalCaverns", None, "Crystal Caves", "secret world D"),
    ("SkyCity", None, "Himmelstadt", "secret world C"),
    ("Special", None, "SPECIAL", "the bonus world 10"),
    ("WSEL", None, "World select", "Newer's hub of pipes to every world; Deluxe joins its worlds on one 3D map"),
]


def port_one(slot, kp_map, region, name, kpmap_dir, game_dir, romfs_root, out_dir, fit_slots="hook"):
    """Import one Newer world into <out_dir>/<slot> <name>/ as a World Maker map. Returns (project, report, path)."""
    from . import koopatlas, rules
    world = int(slot[1])
    variant = slot[2:] if slot[2:] in ("a", "b") else ""
    folder = Path(out_dir) / ("%s %s" % (slot, name.replace("'", "")))
    rom = Path(romfs_root) if romfs_root else None
    exists = (lambda f: (rom / f).exists()) if rom else None
    p, rep = koopatlas.import_map(Path(kpmap_dir) / (kp_map + ".kpmap"), folder, world=world, fit_slots=fit_slots,
                                  course_exists=exists, game_dir=game_dir, region=region, name=name)
    p.variant = variant
    p.name = name
    if slot not in rules.SLOTS:
        raise ValueError("%s is not a Deluxe world archive" % slot)
    target = folder / ("%s.kdxmap" % name.replace("'", "").replace(" ", "_"))
    p.save(str(target))
    return p, rep, target


def port_all(kpmap_dir, game_dir, romfs_root, out_dir, mod_root=None, only=None, log=print):
    """Import (and with mod_root, export) every world of PLAN. Returns ([{slot, name, project, errors, warnings,
    summary, unpacked, archive}], the slot hook's plan for all worlds: rules.global_slot_plan)."""
    from . import project2d, rules
    ported = []
    for slot, kp_map, region, name in PLAN:
        if only and slot not in only:
            continue
        p, rep, target = port_one(slot, kp_map, region, name, kpmap_dir, game_dir, romfs_root, out_dir)
        ported.append((slot, name, kp_map, p, rep, target))
    link_notes = link_worlds([(slot, p) for slot, _n, _m, p, _r, _t in ported], romfs_root)
    for note in link_notes:
        log(note)
    # the two maps of a world (W5a/W5b, W8a/W8b, W9a/W9b) are planned together by the slot hook
    names = {slot: list(p.game_nodes()[0]) for slot, _n, _m, p, _r, _t in ported}
    for slot, _n, _m, p, _r, target in ported:
        other = [s_ for s_ in names if s_ != slot and s_[:2] == slot[:2]]
        p.partner_names = [q for s_ in other for q in names[s_]]
        p.save(str(target))
    worlds = {}
    for slot, _n, _m, p, _r, _t in ported:
        nodes = p.game_nodes()[0]
        opn, ops = rules.opens_of(p.point_rows(nodes))
        w = worlds.setdefault(p.world, [[], set(), set()])
        w[0] += list(nodes); w[1] |= opn; w[2] |= ops
    plan = rules.global_slot_plan({w: tuple(v) for w, v in worlds.items()})
    out = []
    for slot, name, kp_map, p, rep, target in ported:
        chk = p.check()
        row = {"slot": slot, "name": name, "map": kp_map, "project": str(target), "import": rep,
               "errors": list(chk.errors), "warnings": list(chk.warnings), "summary": list(chk.summary),
               "points": len(p.point_rows(p.game_nodes()[0])), "routes": len(p.routes), "unpacked": None,
               "archive": None, "slots": {"%d%s" % (c, " secret" if sc else ""): v for (w, c, sc), v in plan.items()
                                          if w == p.world}}
        if mod_root and not chk.errors:
            row.update(export_one(target, romfs_root, mod_root))
        log("%s %s: %d points, %d routes, %d errors%s" % (slot, name, row["points"], row["routes"], len(row["errors"]),
                                                           (", %.2f MB unpacked" % (row["unpacked"] / 1e6))
                                                           if row["unpacked"] else ""))
        out.append(row)
    return out, plan


def link_worlds(ported, romfs_root):
    """Make the vanilla ways between worlds hold together when several worlds are replaced at once.

    In the game a world's exit opens points and paths inside the next world's map: World 1's castle opens W201 and the
    paths RW2J0W2M0 and RW2M0W201 of World 2, and its walk ends on World 2's junction W2J0. With only World 1 replaced,
    World 2 is vanilla and has them; with World 2 ported too it does not, and the game is left with names no map has
    (the ported worlds crashed in game). So, for every ported world A whose castle leads on:
    - the junction points its walk ends on (W2J0, W3J1, W5J3, W8J0...) are added to the next world B at their vanilla
      place, with a hidden road to the point the vanilla path goes to (RW2J0W2M0: W2J0 to W2M0);
    - the other paths and points it opens in B are kept when B has them, else left out (A.link_drop): B's own roads
      from its start are open from the start anyway.
    Worlds that are not ported keep the vanilla links. Returns notes."""
    from . import koopatlas, romfs, rules
    from .world import World
    root = romfs.normalize(str(romfs_root)) if romfs_root else None
    if root is None:
        return ["No ROMFS: the links between worlds were not checked."]
    tmpl = {wid: World(p) for wid, p in romfs.worlds(root)}
    by_world = {}
    for slot, p in ported:
        by_world.setdefault(p.world, []).append((slot, p))
    notes = []

    def owner(name):
        """The ported project whose map should hold this name (by its world digit), or None (vanilla world)."""
        if len(name) < 2 or not name[1].isdigit():
            return None
        cands = by_world.get(int(name[1]), [])
        if not cands:
            return None
        for slot, q in cands:                                    # the map whose template has it (W5J3: W5a)
            if name in tmpl[slot].point_ids or tmpl[slot].has_bone(name):
                return slot, q
        return cands[0]

    def paths_of(q):
        return {pid for pid, _w in rules.map_paths(q.point_rows(), q.route_rows())}

    for slot, a in ported:
        if not getattr(a, "link_next", False) or slot not in tmpl:
            continue
        t = tmpl[slot]
        tnames = set(t.point_ids)
        foreign = lambda n: n not in tnames and n[:1] == "W" and n[1:2] != str(t.number)
        drop = []
        for trow in t.point_rows:
            pid = trow[1]
            if pid not in a.nodes or t.position(pid) is None:
                continue
            for ncol, pcol in ((3, 4), (6, 7)):
                tp = rules.paths(trow[pcol] if len(trow) > pcol else "")
                if not any(foreign(e) for x in tp for e in (rules.route_ends(x) or ())):
                    continue
                walk_ends = {e for x in tp for e in (rules.route_ends(x) or ()) if foreign(e) and
                             any(not foreign(o) for o in rules.route_ends(x))}
                for path in tp:
                    ends = rules.route_ends(path) or ()
                    far = [e for e in ends if foreign(e)]
                    if not far:
                        continue
                    hit = owner(far[0])
                    if hit is None:
                        continue                                  # a vanilla world: it has the vanilla names
                    bslot, b = hit
                    tb = tmpl[bslot]
                    near = [e for e in ends if not foreign(e)]
                    if near:                                      # the walk out of A: its far end must be in B
                        e = far[0]
                        if e not in b.nodes:
                            pos = tb.position(e)
                            if pos is None:
                                drop.append(path)
                                continue
                            b.nodes[e] = {"kind": "link" if e[2:3] == "J" else "stop",
                                          "x": min(max(pos[0] / b.tile_size, 0.0), b.width - 0.01),
                                          "y": min(max(pos[2] / b.tile_size, 0.0), b.height - 0.01),
                                          "pos": list(pos), "camera": [0.0, 0.0, 0.0],
                                          "row": ["", e, "", "", "", "", "", "", ""], "junction": True,
                                          "junction_from": a.world}
                            notes.append("%s: junction %s added (World %d's way on ends there)." % (bslot, e, a.world))
                        continue
                    x, y = ends                                   # a path inside B
                    junc = [e for e in (x, y) if b.nodes.get(e, {}).get("junction")]
                    if junc and x in b.nodes and y in b.nodes:    # from a junction we added: a hidden road
                        if path not in paths_of(b):
                            b.routes.append({"id": "R%s%s" % (x, y), "type": "道", "kp": "Invisible", "hidden": True})
                            notes.append("%s: hidden road %s from the junction." % (bslot, path))
                        continue
                    if not (x in b.nodes and y in b.nodes and path in paths_of(b)):
                        drop.append(path)
                for n in rules.split(trow[ncol] if len(trow) > ncol else ""):
                    hit = owner(n) if foreign(n) else None
                    if hit is not None and n not in hit[1].nodes:
                        drop.append(n)
        add = []
        for bslot, b in ported:
            start = b.start_node()
            for jn, n in list(b.nodes.items()):
                if n.get("junction_from") != a.world or not start or jn == start:
                    continue
                if any(jn in (rules.route_ends(r["id"]) or ()) for r in b.routes):
                    continue
                b.routes.append({"id": "R%s%s" % (jn, start), "type": "道", "kp": "Invisible", "hidden": True})
                add.append("R%s%s" % (jn, start))
                notes.append("%s: hidden road R%s%s, so World %d's way on reaches the start." % (bslot, jn, start, a.world))
        a.link_add = sorted(set(add) | set(getattr(a, "link_add", []) or []))
        a.link_drop = sorted(set(drop))
        if a.link_drop:
            notes.append("%s: the way on leaves out %s (not in the next world's map)." % (slot, ", ".join(a.link_drop)))
    for slot, b in ported:
        if any(n.get("junction") for n in b.nodes.values()):
            koopatlas.fix_directions(b)
    return notes


def export_one(kdxmap, romfs_root, mod_root):
    """Export a ported World Maker map into mod_root. Returns {unpacked, archive, export_errors, export_warnings}."""
    from . import export2d, romfs
    from .maker2d import WorldMaker
    root = romfs.normalize(str(romfs_root))
    model_dir = romfs.model_dir(root)
    mk = WorldMaker()
    if not mk.open(str(kdxmap)):
        return {"export_errors": ["could not open %s" % kdxmap]}
    slot = mk.project.slot
    template = next(p for wid, p in romfs.worlds(root) if wid == slot)
    notes = []
    for attempt in range(4):
        try:
            written, rep = export2d.export_world(mk.project, mk.render_image(with_nodes=False, moving=False), template,
                                                 Path(mod_root), model_dir)
            break
        except export2d.ExportError as e:
            fix = castle_side_fix(mk.project, str(e), attempt)
            if not fix and "has no free direction" in str(e) and mk.project.link_next:
                mk.project.link_next = False             # every side of the castle has a road (W6a)
                fix = ("the castle has a road on every side, so the vanilla walk on to the next world was left out "
                       "(Castle leads on unticked); free one side of the castle in the World Maker to get it back")
            if not fix:
                return {"export_errors": [str(e)], "export_notes": notes}
            notes.append(fix)
            mk.project.save(str(kdxmap))
    else:
        return {"export_errors": ["the castle's way on to the next world found no free side"], "export_notes": notes}
    return {"unpacked": rep.unpacked_size, "archive": str(written[0]), "export_errors": list(rep.errors),
            "export_warnings": list(rep.warnings), "export_summary": list(rep.summary), "export_notes": notes}


def castle_side_fix(p, message, attempt):
    """When the vanilla walk on to the next world (copied next to the castle on export) leaves the castle the same
    way as one of the map's roads, give that road a bend to another side. Returns a note, or None."""
    import re
    from . import rules
    m = re.search(r"Routes (R\w{8}) and (R\w{8}) leave (W\w{3}) in the same direction \((\w+)\)", message)
    if not m:
        return None
    mine = [r for r in p.routes if r["id"] in (m.group(1), m.group(2))]
    castle, way = m.group(3), m.group(4)
    if not mine or castle not in p.nodes:
        return None
    r = mine[0]
    a, b = rules.route_ends(r["id"])
    other = b if a == castle else a
    sides = [(0, -1), (0, 1)] if way in ("east", "west") else [(1, 0), (-1, 0)]
    dx, dy = sides[attempt % 2]
    c, o = p.nodes[castle], p.nodes[other]
    k = p.add_node("bend", c["x"] + dx * 0.9 + (o["x"] - c["x"]) * 0.1, c["y"] + dy * 0.9 + (o["y"] - c["y"]) * 0.1)
    extra = {x: r[x] for x in ("kp", "hidden") if x in r}
    p.routes = [x for x in p.routes if x["id"] != r["id"]]
    made = [p.add_route(castle, k, r["type"]), p.add_route(k, other, r["type"])] if a == castle else         [p.add_route(other, k, r["type"]), p.add_route(k, castle, r["type"])]
    for x in p.routes:
        if x["id"] in made:
            x.update(extra)
    return "%s: road %s got a bend (%s) so the walk on to the next world keeps the %s side." % (castle, r["id"], k, way)

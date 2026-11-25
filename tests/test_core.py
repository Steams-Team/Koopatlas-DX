#!/usr/bin/env python3
"""
Koopatlas DX core tests, run against the game's real files.

    python tests/test_core.py [folder with CS_*.szs, *OffsetData*.bin and the .msbt files]

The folder defaults to the KDX_SAMPLES environment variable. Game files are not included with Koopatlas DX.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kdx import archive, msbt, offsets, world as W, names as N

SAMPLES = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KDX_SAMPLES", "samples"))
results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    print(("PASS " if ok else "FAIL ") + label + ((" -- " + str(detail)) if (detail and not ok) else ""))


def find(pattern):
    hits = sorted(SAMPLES.glob(pattern))
    return hits[0] if hits else None


if not SAMPLES.is_dir():
    raise SystemExit("Sample folder %s not found (pass it as an argument)." % SAMPLES)

# ------------------------------------------------------------------------------------------- offset tables
for n in range(1, 10):
    p = SAMPLES / ("MarioOffsetDataW%d.bin" % n)
    q = SAMPLES / ("LookOffsetDataW%d.bin" % n)
    if p.exists():
        raw = p.read_bytes()
        check("MarioOffsetDataW%d: parses and writes back byte-identical" % n, offsets.MarioOffsetTable.parse(raw).to_bytes() == raw)
    if q.exists():
        raw = q.read_bytes()
        check("LookOffsetDataW%d: parses and writes back byte-identical" % n, offsets.LookOffsetTable.parse(raw).to_bytes() == raw)

t = offsets.MarioOffsetTable.parse((SAMPLES / "MarioOffsetDataW1.bin").read_bytes())
ids = [r.name for r in t.block_records(0)]
t2 = offsets.MarioOffsetTable.parse(t.to_bytes())
t2.sync(0, ids[:3] + ["W1Z9"] + ids[3:])
check("sync inserts a new point's record at its row position, keeping the others' values",
      [r.name for r in t2.block_records(0)][:5] == ids[:3] + ["W1Z9", ids[3]] and t2.records[3].vector == (0.0, 0.0, 0.0)
      and t2.records[4] == t.records[3])
t2.sync(0, ids)
check("sync back to the original list restores the original bytes", t2.to_bytes() == t.to_bytes())
try:
    t2.sync(0, ["W1%02d" % i for i in range(129)]); over = False
except offsets.OffsetError:
    over = True
check("more than 128 points in one archive is refused", over)
try:
    t2.sync(0, ["W1001"]); bad = False
except offsets.OffsetError:
    bad = True
check("a point ID that is not 4 characters is refused", bad)
try:
    offsets.MarioOffsetTable.parse(b"x" * 100); wrong = False
except offsets.OffsetError:
    wrong = True
check("a file of the wrong size is refused", wrong)

# ------------------------------------------------------------------------------------------- names
wn, cn = SAMPLES / "world_name.msbt", SAMPLES / "course_name.msbt"
if wn.exists() and cn.exists():
    names = N.Names.from_msbt_files(wn, cn)
    check("world names read from world_name.msbt", names.world(1) == "Acorn Plains" and names.world(9) == "Superstar Road")
    check("level name for W101 (1-1)", names.point("W101")[0] == "Acorn Plains Way")
    check("tower and castle names (W1T0, W1C0)", names.point("W1T0")[0] == "Crushing-Cogs Tower" and names.point("W1C0")[0] == "Lemmy's Swingback Castle")
    check("path stops have no level name", names.point("F008") == (None, None))
common = find("CommonMsg_*.szs")
if common:
    nz = N.Names.from_szs(common)
    check("names read straight from %s" % common.name, nz.world(3) == "Sparkling Waters" and len(nz.courses) > 50)

# ------------------------------------------------------------------------------------------- a real world
w1_path = find("CS_W1[._]Nin_NX_NVN.szs")
if w1_path:
    w = W.World(w1_path)
    w.load_offsets(SAMPLES)
    check("World 1 loads: 72 points, 134 routes", len(w.point_ids) == 72 and len(w.routes) == 134)
    check("W101 is where its bone is (180, 60, -60)", all(abs(a - b) < 1e-3 for a, b in zip(w.position("W101"), (180, 60, -60))))
    check("waypoints are found (K bones not in the point table)", len(w.waypoint_ids) > 50 and all(k.startswith("K") for k in w.waypoint_ids))
    check("no problems in the vanilla world (with its MarioOffset table)", w.problems() == [], w.problems())
    check("unedited world: nothing to export", not w.dirty)

    tmp = Path(tempfile.mkdtemp())
    check("exporting an unedited world writes nothing", w.export(tmp) == [])

    w.set_point_cell("W101", 2, "restart")
    w.set_route_type("RW101W102", "坂")
    before = bytes(w.bfres_bytes)
    w.move_point("W102", (430.0, 60.0, -75.0))
    after = bytes(w.bfres_bytes)
    diff = [i for i in range(len(before)) if before[i] != after[i]]
    check("moving a point puts it exactly where asked", all(abs(a - b) < 1e-3 for a, b in zip(w.position("W102"), (430, 60, -75))))
    check("moving a point changes at most the 12 bytes of that bone's position, nothing else", 0 < len(diff) <= 12 and len(before) == len(after), len(diff))
    check("other points do not move", all(abs(a - b) < 1e-3 for a, b in zip(w.position("W101"), (180, 60, -60))))
    rec = w.mario_record("W101")
    w.set_mario("W101", vector=(0.0, 0.0, 0.0), flags=1)
    check("the MarioOffset record can be edited", w.mario_record("W101").vector == (0.0, 0.0, 0.0) and w.mario_record("W101").flags == 1)

    written = w.export(tmp)
    names_w = sorted(p.name for p in written)
    check("export writes the archive and the offset table into the LayeredFS Model folder",
          names_w == ["CS_W1.Nin_NX_NVN.szs", "MarioOffsetDataW1.bin"] and all("0100EA80032EA000" in str(p) and p.parent.name == "Model" for p in written), names_w)
    m_new = (written[1] if written[1].name.startswith("Mario") else written[0]).read_bytes()
    m_old = (SAMPLES / "MarioOffsetDataW1.bin").read_bytes()
    changed_slots = sorted({i // 21 for i in range(len(m_old)) if m_old[i] != m_new[i]})
    check("only W101's record (slot 1) changed in MarioOffsetDataW1.bin", changed_slots == [1], changed_slots)

    w2 = W.World(next(p for p in written if p.name.startswith("CS_")))
    check("the exported world opens with the edits", w2.row_of("W101")[2] == "restart" and w2.route_row("RW101W102")[1] == "坂"
          and all(abs(a - b) < 1e-3 for a, b in zip(w2.position("W102"), (430, 60, -75))))
    orig = dict(archive.read_szs(w1_path)[0])
    new = dict(archive.read_szs(next(p for p in written if p.name.startswith("CS_")))[0])
    check("the shader archive is byte-identical in the export", new["Wii_UBER.Nin_NX_NVN.bfsha"] == orig["Wii_UBER.Nin_NX_NVN.bfsha"])
    check("the CSVs keep the game's format (UTF-8 without BOM, CRLF)", not new["pointW1.csv"].startswith(b"\xef\xbb\xbf") and b"\r\n" in new["pointW1.csv"])
    unchanged_rows = [r for r in W.csv_decode(new["pointW1.csv"]) if r[1] != "W101"]
    check("unedited point rows are unchanged", unchanged_rows == [r for r in W.csv_decode(orig["pointW1.csv"]) if r[1] != "W101"])
    try:
        w.export(SAMPLES.parent.parent) if False else None
        w_src = W.World(w1_path); w_src.set_point_cell("W101", 2, "x")
        fake_root = Path(tempfile.mkdtemp())
        model = fake_root / "atmosphere" / "contents" / "0100EA80032EA000" / "romfs" / "Model"
        model.mkdir(parents=True)
        import shutil
        shutil.copy(w1_path, model / w1_path.name)
        w_src2 = W.World(model / w1_path.name); w_src2.set_point_cell("W101", 2, "x")
        w_src2.export(fake_root); refused = False
    except W.WorldError:
        refused = True
    check("exporting into the folder the world was opened from is refused", refused)

# ------------------------------------------------------------------------------------------- the game's rules
from kdx import rules
check("level files from point names (rules from the reverse engineering)",
      [rules.level_file(n) for n in ("W101", "W1T0", "W1S0", "W1C0", "W1K1", "F001")] ==
      ["Course/1-1.sarc", "Course/1-21.sarc", "Course/1-13.sarc", "Course/1-23.sarc", "Course/1-25.sarc", None])
if w1_path:
    wr = W.World(w1_path)
    rep = wr.report()
    check("World 1 passes the game's rules with no errors and 13 warnings (as wmtool reports)", not rep.errors and len(rep.warnings) == 13, (rep.errors[:2], len(rep.warnings)))
    check("World 1 play-through: 71/72 points reachable, 134/134 routes walkable", any("71/72" in x and "134/134" in x for x in rep.summary), rep.summary)
    chain = wr.path_chain("RW102W1T0")
    check("path RW102W1T0 opens its 19-segment walk", chain and len(chain) == 19 and chain[0] == "RW102F021" and chain[-1] == "RK02hW1T0")
    wr.add_route("RW101W104")
    check("a new route is added as R + start + end, type road", wr.route_row("RW101W104") == ["RW101W104", "道", ""])
    i, row = wr.remove_route("RW101W104")
    check("routes can be removed and put back", wr.route_row("RW101W104") is None and (wr.insert_route(i, row) or wr.route_row("RW101W104")))
    try:
        wr.add_route("RW101W104"); dup = False
    except W.WorldError:
        dup = True
    check("adding an existing route is refused", dup)
    many = W.World(w1_path)
    row = many.row_of("W101"); row[4] = ",".join("RW101W1%02d" % i for i in range(17))
    check("17 paths on one exit is an error (the game keeps 16)", any("the game keeps 16" in e for e in many.report().errors))

# ------------------------------------------------------------------------------------------- World Maker projects
from kdx import project2d as P2
pr = P2.Project(width=32, height=24)
check("a new map: 32×24 tiles, 30 units per tile (about half of Mario), three layers", pr.tile_size == 30.0 and len(pr.layers) == 3 and len(pr.layers[0].data) == 32 * 24)
ch = pr.set_many(0, pr.fill_cells(0, 5, 5, 0))
check("fill covers the whole empty layer", len(ch) == 32 * 24 and all(v == 0 for v in pr.layers[0].data))
ch2 = pr.set_many(0, pr.rect_cells(2, 2, 4, 3, [[8]]))
check("a rectangle of water inside the grass", sum(1 for v in pr.layers[0].data if v == 8) == 6)
check("fill only spreads over equal tiles (the water stays)", len(pr.fill_cells(0, 3, 3, 9)) == 6)
pr.apply(0, ch2, undo=True)
check("undoing the rectangle brings the grass back", sum(1 for v in pr.layers[0].data if v == 0) == 32 * 24)
pr.apply(0, ch, undo=True)
check("undoing the fill restores the empty layer", all(v == -1 for v in pr.layers[0].data))
pr.set_many(0, {(3, 3): 8})
s1 = pr.add_node("start", 2.5, 2.5); l1 = pr.add_node("level", 6.5, 2.5); l2 = pr.add_node("level", 10.5, 2.5); k1 = pr.add_node("bend", 10.5, 5.5)   # an L: a V-shaped bend would leave both ways north (one link per direction)
check("point names follow the game's scheme", (s1, l1, l2, k1) == ("W1M0", "W101", "W102", "K000"), (s1, l1, l2, k1))
pr.add_route(s1, l1); pr.add_route(l1, k1); pr.add_route(k1, l2)
pr.nodes[l1]["row"][4] = "RW101W102"
rep2 = pr.check()
check("a designed world passes the game's rules: no errors, all 3 points reachable through the bend",
      not rep2.errors and any("3/3 points reachable" in x and "3/3 routes walkable" in x for x in rep2.summary), (rep2.errors, rep2.summary))
check("bends are not written to the point table (they exist only as bones and in route ids)", [r[1] for r in pr.point_rows()] == ["W1M0", "W101", "W102"])
pr.rename_node("W102", "W1T0")
check("renaming a point updates its routes and the unlock paths that name it", any(r["id"] == "RK000W1T0" for r in pr.routes) and pr.nodes["W101"]["row"][4] == "RW101W1T0")
import tempfile as _tf
fp = Path(_tf.mkdtemp()) / "test.kdxmap"
pr.save(fp)
pr2 = P2.Project.load(fp)
check("save and load keep tiles, points, routes and settings", pr2.to_json() == pr.to_json())
pr2.resize(40, 20)
check("resizing keeps the tiles that still fit", pr2.width == 40 and pr2.get(0, 3, 3) == 8 and len(pr2.layers[0].data) == 800)

# ------------------------------------------------------------------------------------------- FastYZ
from kdx import yaz0fast
check("FastYZ (built in) loads on this system", yaz0fast.available(), yaz0fast.load_error)
if yaz0fast.available() and w1_path:
    raw = w1_path.read_bytes()
    sarc = yaz0fast.decompress(raw)
    check("FastYZ decompresses the game's archive identically to the pure-Python decoder", sarc == archive._yaz0_decompress_py(raw))
    z = yaz0fast.compress(sarc)
    check("FastYZ output decodes back to the same archive (pure-Python decoder)", archive._yaz0_decompress_py(z) == sarc)
    check("FastYZ output is a normal size (within 25% of the game's file)", len(z) < len(raw) * 1.25, (len(z), len(raw)))

w5b = find("CS_W5b[._]Nin_NX_NVN.szs")
if w5b:
    w = W.World(w5b)
    w.load_offsets(SAMPLES)
    check("W5b uses the second block of MarioOffsetDataW5 and agrees with it", w.block == 1 and w.problems() == [], w.problems())

# clear slots (CourseSlot_getIndex 0x6040E5B0, F0301/F0332)
from kdx import rules as R
check("clear slots: World 1 tracks 14 normal courses (levels 1-5, S/T/C, Toad houses, enemies, X) and 8 secret ones",
      len(R.COURSE_SLOTS[1][0]) == 14 and R.COURSE_SLOTS[1][1] == (0, 1, 2, 3, 4, 12, 20, 22) and
      not R.has_slot(1, 5) and R.has_slot(1, 37) and R.tracked_level_names(1) == ["W101", "W102", "W103", "W104", "W105"])
check("clear slots: 123 normal and 82 secret entries over the nine worlds",
      sum(len(v[0]) for v in R.COURSE_SLOTS.values()) == 123 and sum(len(v[1]) for v in R.COURSE_SLOTS.values()) == 82)
w1p = next(iter(sorted(SAMPLES.glob("CS_W1.Nin_NX_NVN.szs"))), None)
if w1p:
    sw = W.World(w1p)
    sw.add_point("W106", sw.position("W105")); sw.add_route("RW106W1C0"); sw.row_of("W106")[4] = "RW106W1C0"
    errs = [m for m in sw.report().errors if "keeps no normal clear" in m]
    check("clear slots: a level World 1 doesn't track (W106) is an error naming the paths that never open",
          len(errs) == 1 and "RW106W1C0" in errs[0] and "W106" in errs[0], errs)
    check("clear slots: no vanilla W1 point is flagged", not any("keeps no" in m for m in W.World(w1p).report().errors))
    # the framework slot hook (course_slots.cpp), mirrored in kdx.rules
    from kdx import rules as R
    _n = ["W1M0", "W101", "W102", "W103", "W104", "W105", "W106", "W107", "W1T0", "W1C0", "W1S0", "Fq05", "Fq06", "Fr0c"]
    _plan = R.slot_plan(1, _n, set(), {0, 1, 2, 3, 4, 5, 6, 22})
    check("slot hook: W106 borrows W1X0's entry (57), W107 the hidden cannon's (12)",
          _plan.get((5, False)) == 57 and _plan.get((6, False)) == 12, _plan)
    check("slot hook: with the hook a sixth level counts as tracked", R.has_slot(1, 5, names=_n) and not R.has_slot(1, 5))
    check("slot hook: vanilla point names with Fc/Fb (W9b's Fc21) are not markers", not R.marked_courses(["Fc21", "Fb01"])
          and R.slot_marker(5) == "Fq05" and R.lend_marker(12) == "Fr0c")
    _gp = R.global_slot_plan({8: (["W8M0", "W801", "W802", "W803", "W804", "W8B0", "W8B1", "W805", "Fq04"], {0, 1, 2, 3, 4, 41, 42}, set()),
                              9: (["W9M0", "W901"], {0}, set())})
    check("slot hook: a world with no entry left borrows an unused one of another world (World 9 first)",
          _gp.get((8, 4, False)) is not None and _gp[(8, 4, False)][0] == 9, _gp)
    check("slot hook: Toad houses and enemy courses never lend unless told (Fr)",
          all(v is None or v[1] not in range(23, 34) for v in R.global_slot_plan({1: (["Fq05", "W101"], {0}, set())}).values()))

passed = sum(ok for _, ok in results)
print("\n%d/%d passed" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)

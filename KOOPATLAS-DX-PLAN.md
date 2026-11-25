# Koopatlas DX — plan for a custom world tool for New Super Mario Bros. U Deluxe

Koopatlas DX is a world editor and replacer for **NSMBU Deluxe on Switch**, with two ways to build a world:

- **Method A — 3D (the main focus):** edit and replace the game's real 3D world maps, with the same structure the
  game uses, so results look like the original game and run on modded hardware.
- **Method B — 2D (Koopatlas-style):** paint a top-down map from tiles and doodads; the tool turns it into a valid 3D
  world archive automatically.

Both methods share one back end (reading, validating and writing world archives), so a 2D world is just another way
of producing the same files.

This plan is built on what I found in the 13 world archives you sent. Everything in section 1 was **checked against
those files**; anything that is still a guess is marked *hypothesis*.

---

## 1. How the world system works (from your files)

### 1.1 The files

Each world is one archive in `romfs/Model/`: `CS_W1`, `CS_W2a`, `CS_W3`, `CS_W4`, `CS_W5a`, `CS_W5b`, `CS_W6a`, `CS_W7`,
`CS_W8a`, `CS_W8b`, `CS_W9a`, `CS_W9b` (each `.Nin_NX_NVN.szs`), plus `CS_WorldMap` for the world-select screen.

Every world archive is a Yaz0-compressed, little-endian SARC with data aligned to **0x2000**, containing exactly:

| Member | What it is |
|---|---|
| `pointWn.csv` | the map points (levels, houses, path stops) and what clearing each one unlocks |
| `routeWn.csv` | the paths between points and their type |
| `output.bfres` | every model of the world (Switch BFRES **8.1.1**), with its textures embedded as `textures.bntx` |
| `Wii_UBER.Nin_NX_NVN.bfsha` | the shaders, compiled **per world**, that the world's materials use |

### 1.2 The models inside `output.bfres`

Every world follows the same pattern:

| Model | Role | Evidence |
|---|---|---|
| `CS_Wn` | **the map-point model**: one bone per point and per path waypoint, each with a small marker mesh named `<point>__mtCourseOBJ` | 72 of 72 World 1 points have a bone of the same name; every world: 100% |
| `CS_Wn_World` | **the terrain**: one bone, 3k–182k triangles | World 1: 106,736 triangles |
| `CS_Wn_Synchro` | animated decoration (trees, flowers…) | World 1: bones like `big_donguri_treeleaf`, `leef`, `fliower_20`, and a skeletal animation named `CS_W1_Synchro` |
| `CS_Wn_<effect>` | world effects: Ocean, CloudBG, Fog, Doku (poison), Quicksand, Aurora, Magma, Trap, Universe, Castle, DarkCloud… | per world, see appendix |
| `R<from><to>` | **special routes** (lifts, coral, clouds…); some have a skeletal animation and an `_fts` texture SRT animation (moving/scrolling textures) of the same name | World 1: `RW103W104` (bones `lift1st…lift4th`, `stem1st…`) and `RW102W1S0` have both; `RFd02W1X0` and `RW1S0W3J0` have none |

**The points really are in the model.** The point bones sit on the terrain surface: in World 1 the median height
difference between a point and the terrain under it is 0.9 units, many are exactly 0. So the map-point model and the
terrain share one coordinate space, and an editor can show and move points in their true 3D positions. (My current
viewer already places all 134 located World 1 points this way; see `world1_current_viewer.png`.)

### 1.3 Points and waypoints

Point IDs are 4 characters. Families seen across all worlds (counts are over all 12 worlds):

| Family | Count | Likely meaning (*hypothesis* unless obvious) |
|---|---|---|
| `W<world><2 digits>` (W101…) | 54 | levels |
| `W<world>T<n>` / `C<n>` | 8 / 8 | tower / castle |
| `W<world>K<n>` | 21 | Toad houses |
| `W<world>J<n>` | 18 | junctions to other worlds (`RW1C0W2J0` leads to World 2) |
| `W<world>M<n>`, `S<n>`, `G<n>`, `A<n>`, `B<n>`, `X<n>` | 9, 5, 4, 2, 2, 1 | special points (start, warps, airship…) |
| `F…` (F0…F7, Fa, Fb, Fc, Fd, Ff, Fj, Fk, Fl, Fp, Fs, Fx, Fy) | 427 | path stops and special stops (`Fd` = pipes: flag `dokan`) |
| `K…` (K011, K20b…) | (bones only) | **path waypoints**: the bends a route passes through; not in the point table |

Routes are named `R` + start point + end point, always 9 characters (`RK091K092`, `RF008W1C0`). A path from one stop
to the next is a chain of routes through K waypoints. A few route ends per world have no bone: they are in **another
world** (World 1's `W2J0`, `W3J0`), so routes cross worlds.

### 1.4 `pointWn.csv` (9 columns, all 561 rows in all worlds)

| Column | Content | Example (W102) |
|---|---|---|
| 0 | index | `2` |
| 1 | point ID | `W102` |
| 2 | flags (comma list) | (empty); elsewhere `restart`, `W1Item,fix,stop`, `dokan`, `merge,restart,stop` |
| 3 | points unlocked (first unlock) | `W1T0,W1K1` |
| 4 | routes revealed for it, in order | `RW102W1T0` |
| 5 | (always empty so far) | |
| 6 | points unlocked (second unlock) | `W1S0` |
| 7 | routes revealed for it | `RW102W1S0` |
| 8 | (always empty so far) | |

*Hypothesis:* columns 3–4 are the normal exit and 6–7 a second (secret) exit. Only some points use 6–7 (in World 1
only W102), which fits, but it is not confirmed. Flags seen: `stop` 46, `restart` 28, `W<n>Circle` 23, `W<n>maze` 21, `merge` 15, `noUI` 13, `fixX`/`fixY`,
`RainbowD` 12, `focus` 11, `dokan` 9, `water` 8, `W<n>Enemy0/1/A/B/C`, `W<n>PreDemo`, `switchA`/`switchB`, `nonstop`,
`link1`, `W<n>trapA/B`, `onestop`, `W<n>Return`, `shadowDemo`, `boarding`, `station`, `coaster`… Their exact effects
must be confirmed in game or in the game's code.

### 1.5 `routeWn.csv`

Three columns: route ID, route type, an empty column. World 1 uses the types 道 path, 坂 slope, ジャンプ jump,
雲 cloud, 石 stone (other worlds not yet tallied). Saved as **UTF-8 without BOM, CRLF**. (Writing a BOM, which the old route editor's CSV export
does, may break the game; Koopatlas DX must keep the original format, as WorldMapDX's *Save SZS copy* already does.)

### 1.6 Shaders: the key hardware constraint

Every material in every world points at a shading model in that world's own `Wii_UBER` shader archive:
`nw4f_cs_shader` (423 materials, the general world shader), `nw4f_cs_route_shader` (227, the paths),
`nw4f_cs_water_shader`, `nw4f_cs_water_edge_shader`, `nw4f_cs_water_pillar_shader`, `nw4f_kakiwari_shader` (painted
backdrops), plus effect shaders made for one world (`CS_W4_Aurora_Aurora01`, `CS_W8b_Magma_mtLava`…).

The game cannot compile new shaders, and each world archive only contains the shaders its own materials need. So:
**every custom material must be a copy of an existing material (same shading model and options) with new textures and
parameters, and the world must ship a shader archive that contains those shading models.** This is the single most
important rule for working on real hardware, and the tool enforces it.

### 1.7 Lightmaps

The point markers use textures `courseOBJ`, `lmDifCSW1a`, `lmSpcCSW1a`: per-world **lightmaps**. Custom terrain needs
either new lightmaps (baked) or a neutral lightmap; the tool will offer both.

### 1.8 Other parts of the world system (in your ROMFS tree, not yet examined)

| File | Probably | Status |
|---|---|---|
| `Model/LookOffsetDataW1–W9.bin` | sparse per-world camera data | partly decoded, see 1.9 |
| `Model/MarioOffsetDataW1–W9.bin` | one record per point: a vector and flags | **decoded**, see 1.9 |
| `Model/CourseSelectScene.Nin_NX_NVN.szs` | the world-map **UI layout** (not 3D) | examined, see 1.9 |
| `Model/KinopicoMdl/Anm`, `W4/W6/W8_Fly_Boss`, `W7_shiroboss_bg_*`, `PreGameStoryW1/W8` | map actors: Toads, airships, bosses, story scenes | not examined |
| `Layout/`, `Message/` | world-map UI and world/level names | not examined |
| the game's code (main NSO) | the unlock rules, flag meanings, how point IDs become levels, save data | needs reverse engineering |

Note that the offset files are numbered by world (W1–W9), not by archive (W5a/W5b), so the a/b archives of one world
share them.

### 1.9 The offset tables and the world-map UI (from the files you sent)

**`MarioOffsetDataW1–W9.bin`** (5,376 bytes each, every world): a fixed table of **256 records of 21 bytes**.

| Bytes | Content |
|---|---|
| 0–4 | point ID: 4 characters + a zero byte (unused slots are named `W000`) |
| 5–16 | three floats (x, y, z) |
| 17–20 | a 32-bit value: mostly 0–3, also 5, 8, 32, 64, 65, 72 (flags or a mode) |

Every table lists **every point of its world number and nothing else**, covering both archives of a split world (World 5:
85 records = 56 in W5a + 29 in W5b). **Slots 0–127 are the first (or only) archive's points and slots 128–255 the "b"
archive's, and within each block the records are in exactly the same order as that archive's `pointWn.csv` rows**
(checked for all 9 tables). Unused slots are `W000` followed by 17 zero bytes. The vectors are on the map's scale (hundreds of units, like the spacing between
points), and the y value often equals the point's own height (W101, W102, W103, F001). *Hypothesis:* a camera or player
placement used when standing on that point; to be confirmed by changing one record and testing in game.

**Consequence for custom worlds:** adding, removing or reordering a point must update this table in step, and each
archive can hold at most **128 points**.

**`LookOffsetDataW1–W9.bin`** (744 bytes each): almost entirely zeros. Only World 1 (two values: 50 and 12) and World 3
(four values: −140, −104, −172, −260, exactly nine floats apart) contain data. The layout is not settled; a short header
followed by 36-byte records of three vectors fits World 3. Probably a few camera look-at overrides. Koopatlas DX keeps
these files unchanged until their meaning is tested.

**`CourseSelectScene.Nin_NX_NVN.szs`** is the world-map **HUD**, not a 3D scene: a layout (`CourseSelectScene.bflyt`), six
animations (`Zanki` lives counter, `TemporaryStarCoin`, `LoopScroll`, `InOut`, `Flag`, `InOutArrow`) and one texture
archive. Only needed for UI changes.

**`mario_brothers_yuka.Nin_NX_NVN.szs`**: a small floor model (1 bone, 12 triangles) with its own shader archive and
textures. Its role on the world map is not confirmed.

---

## 2. Rules for working on real hardware

1. **Template first.** Every custom world starts from a vanilla world and changes it; new parts are cloned from
   vanilla parts (materials, vertex formats, shader options, skeletons). Nothing is invented from scratch that the game
   has never seen.
2. **Same container.** BFRES 8.1.1, SARC little-endian with 0x2000 alignment, the same member names, the shader archive
   bundled, CSVs in their original encoding and line endings.
3. **Budgets.** Vanilla worlds are 2–16 MB of models and 1.8–8.9 MB of textures (appendix). The tool warns past the
   largest vanilla world and refuses clearly unsafe sizes.
4. **Validate before export, read back after.** Every export is re-read and checked (structure, every point has a bone,
   every route end exists, every material's shading model is in the shader archive, texture formats are ones the game
   uses).
5. **Never touch the dump.** Projects are separate folders; export goes to a LayeredFS mod folder
   (`atmosphere/contents/0100EA80032EA000/romfs/Model/…`; title ID confirmed from your mod folder).
6. **Test ladder.** Automated checks → emulator → console, at every milestone (section 6). The first console test comes
   early and changes as little as possible.

---

## 3. Architecture

| Layer | What | State |
|---|---|---|
| Archives | Yaz0 + SARC read/write (keeps alignment, byte order) | **done** (WorldMapDX 0.7, tested against SarcLib/FastYZ) |
| BFRES reader | models, skeletons, meshes, materials (with shader assignments), textures list, animation names | **done for reading geometry**; reads all 13 real worlds; shader assignment and animation details to add |
| BFRES writer | write BFRES 8.1.1 | **to build**: use **BfresLibrary** (MIT, the engine inside Switch-Toolbox, proven to read/write real games) as a small command-line engine (`kdx-bfres`); I already compiled it here under .NET 8 and wrote a test file with it |
| Textures | BNTX decode (preview) and encode (export, BC1/BC3/BC7 + swizzle) | decode: available in Yaz0moto's `bntxlib`; encode: to add (pip `etcpak`/`quicktex` or BfresLibrary's BNTX side) |
| Renderer | real-time, textured, animated 3D view | **to build**: PyQt5 + OpenGL. The current Tk viewer proves the data is right but must sample the terrain (see the screenshot) |
| World model | points, waypoints, routes, unlock graph, flags, cross-world links | to build on top of the CSV/bone findings above |
| Projects | `.kdxproj` folder (JSON + assets); undo/redo; templates | to build |
| Export | validate → write archive → read back → LayeredFS folder | partly done (*Save SZS copy*) |

**Why BfresLibrary as the writer:** a world archive has hundreds of materials with shader parameters, animations and
relocation tables; a mistake shows up as a crash on hardware. BfresLibrary already writes these for Switch-Toolbox users.
Koopatlas DX drives it with a scene description; a later pure-Python writer stays possible. The gate before relying on
it: **read every vanilla world and write it back unchanged, then compare**.

---

## 4. Method A — 3D worlds (main focus)

Three levels, from safest to most ambitious. Each is usable on its own.

### Level 1 — Edit the world in place (no new geometry)
- See the whole world, textured and animated (Synchro, route animations), with every point, waypoint and route in place.
- **Move points and waypoints** on the terrain (they snap to the surface), edit flags, unlocks (both unlock pairs), route
  types, cross-world links; graph checks (unreachable points, routes with a missing end, duplicate IDs).
- Export changes **only the numbers**: point bone positions are patched where they are, the CSVs are rewritten in their
  original format. The file layout does not change, which makes this the safest first test on hardware.

### Level 2 — Change the structure
- **Add and remove points, waypoints and routes**: new bones and marker shapes in the map-point model, cloned from
  existing ones (same material, same marker mesh), plus CSV rows.
- Special routes: clone a route model and its two animations (lift, cloud, coral…) and attach it to a new route.
- Re-texture the world (replace BNTX textures with your own images, same formats).
- Needs the BFRES writer (BfresLibrary).

### Level 3 — Replace the terrain (a new world that looks like the game)
- Import terrain from Blender (glTF 2.0, also FBX/OBJ) and convert it into `CS_Wn_World` shapes using **cloned vanilla
  materials** (`nw4f_cs_shader`, water, kakiwari…) with your textures.
- Lightmaps: bake, or use a neutral one.
- Decorations and effects from a library extracted from your own dump (trees, water, clouds, fog…), placed by hand.
- A **Blender add-on** for advanced users: export the terrain together with point empties named like the points
  (`W101`, `F008`, `K011`), so a whole world can be built in Blender and finished in Koopatlas DX.

### Easy for beginners, deep for experts
- Beginners: *New world from template*, drag points on the terrain, pick route types from a list, one-button export with
  a plain-language report.
- Experts: raw CSV view, per-bone transforms, material clone editor (shader options shown read-only, parameters
  editable), texture formats, animation slots, validation details, scripting hooks.

---

## 5. Method B — 2D worlds (Koopatlas-style)

The game already draws a world as a flat textured plane: `CS_WorldMap` is exactly that (a 360 × 360 plane, material
`mtWorldMap`, textures `worldMap` + `lm_01m`). Method B uses the same idea for a whole world:

1. **Paint** the map from tilesets on a grid, with layers (ground, paths, decoration), like Koopatlas.
2. **Doodads**: flat images, optionally standing up as billboards with the game's `nw4f_kakiwari_shader` (painted
   backdrops), for a 2.5D look.
3. **Place points and draw paths** on the 2D map; types and unlocks are edited exactly as in Method A.
4. **Export**: the tool builds a subdivided plane (optionally raised by a height map), bakes the layers into BNTX
   textures, creates the map-point model (bones from your 2D positions), route geometry and the CSVs, then packages
   everything through the Method A back end.

Method B needs Level 2 of the back end (structure writing) and texture encoding; it comes after Method A's first
levels in the roadmap.

---

## 6. Roadmap (each milestone ends with a test gate)

| # | Milestone | Gate |
|---|---|---|
| M0 | Read and understand the world files | **done**: all 13 archives read; points on terrain confirmed; CSV schemas mapped |
| M1 | New viewer: PyQt5 + OpenGL, textures (BNTX), all models, animation preview, cross-world links | **started in 0.1**: OpenGL viewer with every model at full detail, points/waypoints/routes, names; textures and animations still to do |
| M2 | Level 1 editor: move points/waypoints, edit flags/unlocks/route types, in-place export | **built in 0.1** (with MarioOffset editing, undo/redo, checks); waiting for the **first console test**: move one point in World 1, change one route type |
| M3 | Write engine: BfresLibrary `kdx-bfres`; round-trip every vanilla world | written-back worlds identical in content; console test with an unchanged rewritten World 1 |
| M4 | Level 2: add/remove points, waypoints, routes, special routes; texture replacement | console test: a new level point and route in World 1 |
| M5 | Level 3: terrain import, material cloning, lightmaps, Blender add-on | console test: a custom terrain world |
| M6 | Method B (2D) on top of M4/M5 | **started in 0.2**: a 2D map editor (top-down, Koopatlas-style) for existing worlds, sharing selection, edits and export with the 3D view. Still to do: painting new worlds from tiles, which needs M3/M4 · console test: a painted world |
| M7 | The rest of the world system: meaning of the MarioOffset vector and flags (format already decoded), LookOffset layout, HUD (`CourseSelectScene`), map actors, world/level names, how point IDs link to levels | documented formats; edits verified in game |
| M8 | Polish: undo/redo, templates, validation report, LayeredFS export, docs, sample world | release |

Each milestone keeps a test suite like WorldMapDX's (62 checks today), plus an emulator run and a console run.

---

## 7. Open questions (and how to answer them)

| Question | How |
|---|---|
| Exact meaning of each flag and of columns 5/8 | change one at a time and test in game; read the world-map code (your NSO extraction work helps here) |
| Normal vs secret exit in columns 3–4 / 6–7 | test with a level that has both |
| How point IDs map to levels (W101 → 1-1?) | compare with `Course/` names and level-name data; game code |
| Does the game read point positions only from the bones? | M2 console test (move a bone, nothing else) |
| What the MarioOffset vector and flags do; whether the game reads exactly 256 records | change one record (e.g. W101) and test; game code |
| LookOffset layout and meaning | compare W1/W3 in game with the values zeroed |
| How many bones/points/textures the game tolerates | measure vanilla maxima; raise gradually on console |
| Whether BfresLibrary writes 8.1.1 exactly as the game expects | M3 round-trip gate. Known issue to watch: its saver writes a material's texture and sampler counts in the opposite order to its own loader (found while testing it here); harmless when the counts are equal, as in real files |

---

## 8. What I need from you

1. **Decide the UI stack**: PyQt5 + OpenGL (recommended; you already have PyQt5 from Yaz0moto), keeping the tool in
   Python.
2. **Files for M7**: the `Message` archive with world and level names (the HUD layout and offset tables are now in hand).
3. **A console test setup** for M2: which CFW/LayeredFS setup you use, so exports go to the right folder.
4. Optional: anything you know from the private Koopatlas U port about how it handled the map-point model and routes.

---

## Appendix A — the 12 worlds (from your files)

Route ends: how many route endpoints have a bone in the map-point model (the rest are in other worlds).
Animations: skeletal / material.

| World | Points | Routes | Route ends with bone | Terrain triangles | Effect models | Special routes | Animations | Models | Textures |
|---|---|---|---|---|---|---|---|---|---|
| W1 | 72 | 134 | 133/135 | 106,736 | - | 4 | 3 / 2 | 10.8 MB | 5.9 MB |
| W2a | 62 | 142 | 130/134 | 116,704 | CloudBG, Quicksand | 4 | 5 / 2 | 13.2 MB | 8.3 MB |
| W3 | 42 | 172 | 169/172 | 81,855 | Ocean | 8 | 9 / 8 | 11.5 MB | 6.1 MB |
| W4 | 74 | 185 | 183/186 | 143,419 | Aurora | 1 | 3 / 2 | 12.6 MB | 6.3 MB |
| W5a | 56 | 144 | 142/144 | 182,420 | Doku, Fog | 8 | 11 / 4 | 15.9 MB | 8.6 MB |
| W5b | 29 | 82 | 76/76 | 99,323 | Doku, Ocean | 0 | 1 / 4 | 12.6 MB | 7.4 MB |
| W6a | 72 | 175 | 169/171 | 178,739 | Trap | 1 | 4 / 0 | 8.7 MB | 2.9 MB |
| W7 | 72 | 234 | 224/225 | 45,080 | - | 3 | 4 / 0 | 8.0 MB | 4.5 MB |
| W8a | 5 | 2 | 3/3 | 38,682 | Castle, CloudBG, DarkCloud, DarkCloud_Demo1, DarkCloud_Demo2, Ocean, Wall | 0 | 7 / 16 | 11.5 MB | 5.1 MB |
| W8b | 20 | 53 | 53/53 | 79,140 | CloudBG, Magma | 5 | 6 / 3 | 8.1 MB | 4.5 MB |
| W9a | 2 | 1 | 2/2 | 5,360 | - | 0 | 0 / 0 | 2.1 MB | 1.8 MB |
| W9b | 55 | 114 | 110/110 | 3,094 | Universe | 0 | 1 / 2 | 9.6 MB | 8.9 MB |

`CS_WorldMap`: one 360 × 360 plane (`pPlane1` / `polygon0`), material `mtWorldMap`, textures `worldMap`, `lm_01m`;
shading model `wii_uber`.

# Koopatlas DX — roadmap to a full world editor

This plan builds on `KOOPATLAS-DX-PLAN.md` (how the world files work) and on what 0.3 already does. It covers the four
things asked for: easier path editing, camera (MarioOffset) editing with a preview of the camera along the paths,
previews of the world's models and animations, and a tileset-based 2D world editor.

## Where things stand (0.3)

| | Status |
|---|---|
| Path edits in game | **Tested on hardware: they work** (in-place editing is confirmed safe) |
| Yaz0 compression | **FastYZ built in** (MIT, AboodXD), loaded as a small native library: World 1 decompresses in 0.04 s and compresses in 0.06 s; checked against two other decoders. The Windows DLL is cross-compiled and its imports/exports checked, but has not been run on Windows yet; if it fails to load, the built-in code takes over |
| OpenGL | Already OpenGL 3.3 core; no upgrade needed (any GPU from roughly the last 12 years) |
| Model reader | BFRES 5–8. All world and map-actor files are 8.1.1; one 5.0.2 file (`Stage_Stadium00_Env`) holds textures only, so 5.x models are untested |

### What your testing settled

- **MarioOffset is the camera.** Each record is where the camera sits relative to Mario while he is at that point
  (as RouteEdit's *Camera Settings* tab shows). Without the file, the camera follows Mario exactly.
- The fourth value (RouteEdit: "Unknown") is still unknown. In the game's files it is mostly 0–3, sometimes 5, 8, 32,
  64, 65 or 72.
- **LookOffset** is unknown, and RouteEdit does not show it either.

### Corrections from the new files

- `W4/W6/W8_Fly_Boss` are tiny sky-and-cloud backdrops (6–66 triangles) with texture-scroll animations; `W7_shiroboss_bg_D/M/U`
  are pillar and wall layers. They look like **boss-level backgrounds**, not world-map actors.

---

## Part A — Paths: easier editing

**Can be done now, in place (same kind of edit that worked in game):**
- **Draw routes** between existing points/waypoints: click the start, click the end, pick a type. Names follow the game's
  rule (`R` + start + end) automatically. Delete, reverse and re-type routes; change many at once.
- **Unlock chain editor**: for a point, click the routes in order on the map to build "Unlock routes", with the target
  points filled in from the chain's end. The editor checks the chain is continuous and ends at the target.
- **Unlock overlay**: arrows showing which point unlocks which, and highlighting a chain when you select a point.
- **Walk test**: a marker travels the chain from a point to its target at game-like speed, through the waypoints, so you
  can check the route order before testing in game.
- **Path checks**: routes going nowhere, points no route reaches, chains with gaps, duplicate routes.

**One limitation to know:** the visible path on the map is part of the 3D model (the route shader's geometry). A new
route between existing points is walkable, but no path graphic appears until the model writer can add geometry (Part E).

**Needs the model writer:** new points and waypoints (new bones and marker shapes), splitting a route by inserting a
waypoint, deleting points.

## Part B — Camera editing (MarioOffset) and the camera along the paths

- **Camera gizmos** in the 3D view: at each point, a camera icon at *point + offset*, with a line to the point. Drag the
  icon to set the offset; the panel shows the numbers. Copy/paste offsets; apply one offset to several points.
- **Camera preview ("ride along")**: Mario walks a route or an unlock chain and the view shows what the game camera would
  show, using the offsets of the points he passes. How the game blends between points is not known yet, so the preview
  offers a few blending rules (linear between points, hold until the next point, ease) and you pick the one that matches
  the game; the chosen rule then becomes the default.
- **"Follow Mario" check**: show the camera with no offset (what the game does without the file) next to the edited one.
- **Flags experiments**: a helper that exports variants (one point's flag changed at a time) so the fourth value can be
  worked out by testing.
- **LookOffset**: stays a raw editor until testing explains it.

## Part C — Models, textures and animations

- **Textures**: read the world's `textures.bntx` (deswizzle + decode BC1/BC3/BC4/BC5/BC7). This is too slow in pure Python
  for worlds this size, so it goes into a small native helper built the same way as FastYZ (no compiler needed on your PC).
- **Materials**: show each material with its main texture, transparency and vertex colours, so the world looks like
  the game instead of flat colours. Lightmaps optional.
- **Animations**: skeletal (the `Synchro` trees, route lifts), texture scroll (`_fts`: water, clouds), colour (`_fcl`) and
  visibility; play them in the world view (toggle) and in the model viewer.
- **Model browser**: any `.szs` in `romfs/Model`, with orbit view, animation list and timeline (play, pause, scrub, speed),
  material and texture lists (export textures as PNG), skeleton view. Useful for map actors (Toads, airships) and for
  choosing decorations for 2D worlds.

## Part D — The 2D world editor (tileset-based)

The 2D editor is its own workspace for **making new worlds**: you paint the map from tiles like a 2D level editor, place
the points and paths on it, and Koopatlas DX turns it into a 3D world archive the game can load. (The current *2D map*
tab, which edits existing worlds from above, stays.)

### Layout
- **Left — Tileset palette**: tabs per tileset; tiles grouped as ground, paths, water, cliffs and edges, decoration; a
  preview of the brush.
- **Centre — Map canvas**: grid, zoom, pan, rulers; animated tiles play.
- **Right — Layers and properties**: layers with visibility and lock; the selected node's or route's settings (type,
  unlocks, camera).
- **Top — Tools**; **bottom — minimap and status**.

### Layers
Ground · Paths · Water (animated) · Decoration (objects and stamps) · Overlay (clouds, shadows) · Nodes · Routes · Camera.

### Tools
Brush (1×1 to N×N), rectangle, line, fill, eraser, eyedropper; **stamps** (multi-tile objects such as a castle or a forest
patch); **autotile** (edges and corners blend automatically); **path tool** (draw a path between nodes: it paints the path
tiles and creates the route and waypoints); **node tool** (levels, Toad houses, towers, castles, pipes, path stops, with IDs
assigned in the game's style: W1xx, W1T0, W1C0…); **route tool**; **camera tool** (Part B, in 2D); optional **height brush**
for gentle hills; select and move regions; undo/redo everywhere.

### Tilesets
- A tileset is a PNG sheet plus a JSON file: tile size, world units per tile, autotile groups, animation frames,
  categories.
- Koopatlas DX ships a **starter tileset of original art** (grass, sand, water, cliffs, paths, trees). It does not
  include Nintendo art.
- **Import from your game**: turn the world textures from your own dump into tilesets locally, so maps can match the
  game's look.

### Export (needs Part E)
1. Bake the layers into textures (encoded to the formats the game uses, within the vanilla texture budget).
2. Build the terrain mesh (a grid, with heights if used) with UVs.
3. Copy materials from a template world, so the shaders exist in the world's shader archive (the hardware rule from the
   plan).
4. Build the map-point model: one bone and marker per node and waypoint.
5. Write the point/route tables and the camera block, then package and export to LayeredFS.

Paths are painted into the ground texture, so they are visible without separate path geometry.
Checks before export: at most 128 points per archive, texture budget, shader availability, every route connected.

### Preview
The 3D tab shows the generated world live while you paint (plane, textures, nodes, paths, camera), long before export.

## Part E — The model writer

Needed for new points and waypoints, visible new paths, and exporting 2D worlds. As planned: BfresLibrary (the engine
inside Switch-Toolbox, MIT) as a small command-line engine, gated by **writing every vanilla world back unchanged**, then
adding bones and marker shapes, then new terrain and textures.

---

## Progress (updated for 0.4)

- **0.4 done**: paths and camera, built on the reverse engineering of the game's world-map code (wmtool.py): rule checks
  and play-through, real column meanings and limits, level files, flags chooser, picking points for exits, segment
  highlights, connecting/deleting routes, draggable cameras in 2D and 3D, view from a camera, direct dragging of points,
  arrow-key nudging. Still to do from Part B: the ride-along camera preview with selectable blending.
- **0.5 done**: the World Maker (tileset 2D world editor): palette, brush/eraser/fill/rectangle/eyedropper, layers,
  points and routes with the game's naming, rule checks and play-through while designing, starter tileset of original
  art, PNG tile sheets, .kdxmap projects, picture export, a sample world. Tile size 30 units (≈ half of Mario, estimated).
  Also: crash logging and protection after a 2D-tab crash report.
- **0.6 done (moved up, by request)**: the model writer (kdx-bfres on BfresLibrary, round-trip gate passed on
  Worlds 1, 3 and 7) and **exporting World Maker maps to the game** (flat textured terrain, point bones and markers,
  tables, camera table); wmtool reports 0 errors on an exported world. Waiting for the first console test.
- **0.6.1 done**: first console tests. The exported world loaded, then crashed: the W1 map music looks up `W1S0`
  without a null check. Every name the game's code needs per archive is now added as a hidden placeholder.
  Also: the world-link bones are kept, the archive slot is chosen in the World Maker, and the mod-folder path is fixed.
- **0.7 done**: second console test: the world loaded but the player could not move, because walkable paths are path
  bones in the model (`R<from><to>` + walk, `0x60181FC0`). Exports now write them in vanilla's order (World 1's 17
  rebuilt exactly). Castle leads on to the next world, using the vanilla walk moved next to your castle. Terrain
  height: the Height tool, a terrain mesh with hills, points on the ground. Point tables are quoted byte-identically
  to Nintendo's.
- **0.7 console results**: Starter Island (test mod 7) works in game: walking both ways, castle → World 2 → back.
  Getting there took two more fixes, both found in game. The W1 map Yoshi needs its spot (`balloon1`): found with
  Ryujinx's GDB stub (event 116 stuck at step 324). And one neighbour per direction (`0x60178558`).
- **0.7.1 done** (Part A): walk test, Add-flag picker with the world's needs, red rings on problem points.
- **Hills tested in game**: they work.
- **0.8 done** (Parts C and E, plus custom 3D worlds):
  - textures and materials in the 3D view (own BC1-5/RGBA8 decoder, verified on all 1,193 world textures)
  - animations (texture SRT and skeletal) through the model writer
  - the ride-along camera preview
  - OBJ models as the ground of custom worlds (one shape and texture per material)
- **Test mod 9 (3D model) in game**: loaded, but showed only a yellow arc over the sea. The OBJ wound clockwise and
  the game draws front faces only (`gsys_render_state_display_face` front), so the grass was culled and the far cliff
  showed from the inside. Fixed: OBJ triangles are turned to face outwards on loading; the 3D view now culls like the
  game. Also: World 1's tree shadows (`mtLeafDonguriShadow`, a BC4 mask with no lightmap) drawn dark instead of white;
  `kdx-bfres materials` dumps shader options, parameters and sampler wrap modes.
- **Test mod 10 in game**: the island drew (face fix confirmed) but washed out, sand white, and the far west showed a
  void and jagged grass/sand triangles. Causes: white lightmaps and baked light instead of the game's (vanilla
  lightmaps are about 0.4 bright, baked-light alpha about 6), one material per triangle, no sea, and the island's north
  third overlapping World 2's ground after the shift. Fixed in the exporter (template lightmaps kept, neutral baked
  light computed from the copied material, BC1 textures) and in the sample (mod 11: one painted ground, cliffs, sea,
  castles, bridge, south of World 2).
- **0.9 done** (by request): FBX (binary/ASCII) and COLLADA models alongside OBJ and Wii BRRES; the 3D world tab
  (select, move, turn, scale objects and the ground; drag points); scenery objects; the model editor (per-material
  visibility, render mode, texture/colour, two-sided, flip; turn/scale; save as DAE/OBJ); cut-out donors in the model
  writer; the NSMBW world port (World 1 ported, from NSMBW's own model, RouteInfo tables and cob scenery; BrawlCrate as
  the MDL0 reference). Waiting for the first console test: `NSMBW_W1_test_mod_1`.
- **Next**: NSMBW World 1 (test mod 1) and Yoshi Island 3D (test mod 11) in game. Then:
  - NSMBW Worlds 2-9 (split worlds W2a/W5b..., each world's special rows and enemy flags)
  - object animations (NSMBW castle doors and flags, mushroom lifts) through the writer's skeletal animations
  - visible path pieces (`R<path>` models that appear when a path opens)
  - adding and removing points in existing worlds through the writer
  - a model browser (any .szs in romfs/Model) and playing animations of a single model
  - your own models as scenery objects (not only ground), with their own animations
  - the game's lightmaps and shading in the 3D view

## Suggested order

| Version | Contents | Needs the writer? |
|---|---|---|
| **0.4** | **2D world editor, authoring**: tileset palette and tools, layers, nodes, routes, camera, projects, starter tileset, live 3D preview | no (export comes in 0.7) |
| 0.5 | Paths and camera: draw/delete routes, unlock chain editor and overlay, walk test, camera gizmos and ride-along preview, flag experiments | no |
| 0.6 | Looks: textures, materials, animations, model browser, game-texture import for tilesets | no |
| 0.7 | Writer: round trip, new points and waypoints, **2D world export** | yes |
| 0.8 | Polish: docs, sample world, templates | |

Each version ends with a console test of whatever it can export.

## Questions for you

1. **Order**: start with the 2D editor (0.4) as above, or with paths and camera (0.5), which can be tested in game
   straight away?
2. **Tile size**: how big should one tile be in the world? Vanilla worlds span about 6,000 × 3,700 units; 32 units per
   tile would make a 128 × 128-tile map about 4,000 units across.
3. **Writer runtime**: BfresLibrary needs .NET. Is shipping a self-contained engine (about 70 MB) acceptable?
4. **Camera blending**: from your RouteEdit testing, does the camera move smoothly between points, or jump?
5. **RouteEdit**: where is it from? Knowing what it already supports helps avoid duplicating work.

# Koopatlas DX 0.9.1 — world editor for New Super Mario Bros. U Deluxe (Switch)

Koopatlas DX opens the game's world-map archives (`romfs/Model/CS_W*.Nin_NX_NVN.szs`) and lets you edit them in a
**2D map** (seen from above, like Koopatlas) or a **3D view**. Changes are exported into a LayeredFS mod folder; the
game files you opened are never modified.

## Install and run (Windows)

1. Install Python 3.10 or newer from python.org.
2. Double-click `launch_koopatlasdx.bat` (installs PyQt5, PyOpenGL and numpy the first time), or run
   `py -3 -m pip install -r requirements.txt` then `py -3 koopatlasdx.py`.
3. **On first start Koopatlas DX asks for your romfs folder.** Pick the `romfs` folder of your dump (picking the folder
   that contains it, or its `Model` folder, also works). It checks the folder and shows what it found.
4. The **Worlds** panel lists every world with its name (W1 — Acorn Plains, …). Double-click one to open it.
   *File > Open a file by hand…* (Ctrl+Shift+O) still opens any world archive from anywhere.

`BUILD_WINDOWS_EXE.bat` makes a stand-alone `dist\KoopatlasDX\KoopatlasDX.exe`.

## What's new in 0.16.0: all of Newer, more levels per world, every path type (phase 7)
**More levels per world (framework slot hook).** Deluxe keeps clears only for some courses of each world (World 1: 1-1 to 1-5, its tower, castle and cannon). Newer's worlds have 8 or more levels, and beating a level the save doesn't track opens nothing.
- New framework hook `code/framework/course_slots.cpp` (nsmbud-reverse, hooking `CourseSlot_getIndex` at `0x6040E5B0`). A world marks the courses it needs with hidden points `Fq<course>`. Each one borrows the entry of a course that is free, in this order:
  1. its own world's unused tracked courses (W?X0, the cannon, ghost house, towers, castle, airship, normal levels);
  2. courses whose exit opens nothing;
  3. another world's unused entries.
- Toad houses are never borrowed: the game wipes their clears on its own (`reviveTargetKinokoHouse`, F0337). Enemy courses are borrowed only when the map enemies stand on hidden placeholders (`Fr<course>`).
- World Maker: **Map > More levels per world**. The checks plan the hook exactly as the game will. **Change to** lists the levels the hook can still keep.
- The NSMBW World 1 port uses it too (its old one-off hook in `patches/nsmbw-w1` is gone; re-export the port).
- `Fc`/`Fb` would have clashed with vanilla points (W9b has `Fc21`), so the markers are `Fq`/`Fr`.

**Every Koopatlas path type.** All 20 of Newer's path animations now map onto Deluxe's own route actions:
- walk/sand/snow become road/sand/snow;
- water and swim become water steps (水);
- jumps, falls and launch stars become the jump arc;
- ladders keep their facing;
- pipes, doors, caves and invisible paths become hidden roads: no road art, drawn faded, Mario seen at 30% in the walk test.

Each route keeps its Newer kind (tooltip, walk test status). Newer refuses to walk back along jumps, pipes and doors; Deluxe walks both ways, and the route tooltip says so. Hiding Mario in a pipe needs a framework action (port report).

**Newer's real level types.** With the mod folder, the import reads `NewerRes/LevelInfo.bin`: each level's name, its shown number and its kind.
- Kinds: 20 ghost house, 21/22 tower, 23 castle, 27 airship, 36 music house, 37 shop, 38 challenge house, switch palaces.
- 1-15 becomes the tower W1T0, 1-25 the castle W1C0, 1-38 the airship W1A0, and the shop and houses become Toad houses.
- Points carry "Newer 1-1: Palm Beach".
- Newer's levels are Wii levels. Until you put real ones in the mod, a level whose course file Deluxe doesn't have plays a **stand-in Deluxe course**, copied into the mod on export.
- Only names ending in a digit are used: the game registers no path to W10a.
- A new game starts on the map's first node, as in Newer.

**A full port of Newer (World Maker > Port Newer…).** Twelve Newer worlds in Deluxe's twelve archives:

| Slot | Newer world | Unpacked |
| --- | --- | --- |
| W1 | Yoshi's Island | 8.7 MB |
| W2a | Rubble Ruins | 11.0 MB |
| W3 | Mushroom Peaks | 9.2 MB |
| W4 | Sakura Village | 12.4 MB |
| W5a | Freezeflame Glacier | 12.6 MB |
| W5b | Freezeflame Volcano | 11.3 MB |
| W6a | Pumpkin Boneyard | 5.5 MB (its castle has a road on every side: the walk on to World 7 is left out) |
| W7 | Starry Skies | 9.2 MB |
| W8a | Koopa Planet | 10.5 MB |
| W8b | Koopa Core | 6.9 MB |
| W9a | Goldwood Forest | map only (the W9 ground models lack the layout the model writer copies) |
| W9b | Special World | map only (same) |

- Maps that hold several worlds (Fullmap: 290 paths, MountainBackside) are split per world (`koopatlas.world_regions`). Each world is cropped to its own ground, with the real tiles.
- The slot hook is planned for all worlds together, and a world's two maps (W5a/W5b, W8a/W8b) are planned as one.
- `Next Update/Newer port report.md` lists the worlds left out (no archive left), and every part that needs custom code in Deluxe, with its hook address.
- **Fixed: the first port crashed in game** just after the maps loaded. Two causes:
  - The model writer removed every model but the marker and terrain, and cleared every animation. The map's actors still look them up by name: the W8a dark cloud, the W6a trap, and the decorations (quicksand, ocean, aurora, fog, magma...). Now the template's other models and their animations stay, with every bone hidden; only the vanilla road models and road animations go. A world over the size limit is exported again without its optional decorations.
  - **Second crash (shader setup):** every world archive Koopatlas DX wrote asked the game to unpack it with alignment 0 (Yaz0 header bytes 8-11). Every vanilla Model archive asks for 0x2000, and the models and shaders inside need their 4 KB alignment in memory. With one replaced world the heap may happen to give an aligned buffer; with ten it doesn't. Archives now keep the template's alignment (0x2000 for any archive with GPU files).
- **From the first in-game footage of the ported Yoshi's Island (World 1 alone boots and plays):**
  - **Second footage pass (Newer's own source, `Newer-Team/NewerSMBW` Kamek/src/koopatlas):**
    - **Not squashed any more.** Koopatlas draws its maps for a straight-down orthographic camera (`camera.cpp`); Deluxe's map camera looks down at a slant, which squashed circles, fences and trees to half their height. Map > **Front-to-back stretch** (Koopatlas imports: 2) stretches the map that much in depth on export only (`Project.stretched`); the editor keeps the map as drawn.
    - **Trees sway like Newer's.** Koopatlas's Y Scale keeps a doodad's bottom edge in place and X Scale its left edge (`map.cpp` renderDoodadLayer), so a swaying tree stays rooted. The game export and the editor preview now do the same.
    - **Ladders climb.** Deluxe climbs only a route whose ends are less than 3 units apart in x and z (F0322/F0323); the export puts a ladder's top end straight above its bottom end when the top is a point Mario passes through (`export2d.climbable_ladders`).
    - The raised ground levels below are now an import option (`depth=True`), off by default: Newer's art already paints its cliffs.
  - **Depth for Koopatlas maps.** Koopatlas maps stack their ground in named levels (Yoshi's Island: beach, then Forest Lowest / Low / Medium, each a Grass top over a painted Ground cliff face). The import now raises every level 0.8 tiles above the one it stands on, and turns each painted cliff face into a slope down to the level below, so the art's own cliffs stand up as walls in the 3D view and in game (`koopatlas.layer_heights`; Fullmap's separate regions are levelled apart). The ground mesh now has one cell per tile whenever it fits in 16-bit indices.
  - **Sharper ground, no shimmer.** The painted map was exported at 2048x512 with one mip level: squashed (each side rounded down to a power of two) and shimmering at a distance. It is now the nearest power of two a side (up to 4096) with a full mip chain; a world over the size limit gets the largest texture that fits (Yoshi's Island: 4096x1024). The engine writes BC1 mip chains in the Switch layout (checked against the game's own textures; W1/W7 roundtrip unchanged).
  - **No more flickering trees and bushes.** Overlapping moving images (and path art) sat at the same height, so the game's depth test flickered between them. Each one is now lifted 0.5 units above the images it overlaps, in the editor's drawing order (`pictures.quad_layers`).
  - With several worlds replaced, a castle opened junctions and paths (W2J0, RW2J0W2M0...) that the next ported map didn't have. The port now adds each junction to the next world at its vanilla place, with a hidden road to the start (`newerport.link_worlds`).

**Path art sweeps along its path.** Path art now appears along the path towards its destination, like the game's own roads, instead of fading in all at once. The reveal mask runs from 3 to 1 along the path on a 12x12 grid (F0304's MaskRouteAnimation).
- Map > **Path art**: sweeps / fades.
- The Reveal preview and the walk test show the sweep.
- Moving doodads that belong to a path appear with it in the editor. In game they move from the start.

**Map music from your own songs.** A map can play a song added with nsmbudx-music (`CUSTOM_BGM_<nn>`):
- the World Maker's Music box has **Custom song…**;
- it lists the songs it finds in the mod's `Sound.bfsar`;
- the framework's map music hook plays the song through the starter's `prepareBGM` (hidden point `Fn<nn>`, F0339).

**Main editor: a Music field** above the world tree, for vanilla worlds too: another world's part, or a custom song (undoable hidden point).

**Islands and themes.**
- **Round edges**: island corners become quarter circles, in the game too, with a soft shadow on the ground below.
- The cliff box is now a **theme preset**: cliffs, sea colour and road colour.
- **Paint roads** draws thick roads along every route in the theme's colour (not hidden roads or climbs).

**Exports that used to fail.**
- Templates without a plain road material (W5b, W9a) export path art always shown.
- Ground models not on a root bone (W2a, W5b, W8a) keep their moving images still.
- W8a and W9a have no numbered level marker; any point marker now stands in.
- Newer stops are named from F100, so they no longer clash with the vanilla walk on to the next world (W2a's F007).

**RE (nsmbud-reverse):**
- F0337: who reads and wipes the clear entries;
- F0338: what movements Deluxe has, and that its pipe states only serve the warp network;
- F0339: the map music starter's scene label.

## What's new in 0.15.1: Koopatlas worlds that look like Newer, path art, "Change to"
**The real tiles.** Point the import at the mod's Riivolution folder (for Newer: `NewerSMBW`, which has `Maps/`). Koopatlas DX then draws the map from the game's own files:
- `Maps/<map>.kpbin.LZ`: Koopatlas's export, every tile layer already cut into 16x16 sectors of tile numbers. LZ11 is decompressed here.
- `Maps/Texture/<tileset>.bin.LZ`: the tiles as a GX texture (RGB5A3, or RGBA8 for the `_RGBA8` sets), 896 px wide, 32 tiles of 24 px per row in 28 px cells.

Yoshi's Island then looks as it does in Newer: grass plateaus on cliffs, the beach, orange roads and ladders. No Koopatlas tileset archives are needed.
- The importer fills the folder in when the `.kpmap` sits inside it, and remembers your choice.
- Without the folder, the tile layers become a **stand-in**: their shapes drawn as grass tops, cliffs, sand, shore or roads (from the layers' names) on the map's sea.
- Koopatlas's own tileset archives (`.arc`) still work as a second choice.
- The map is now framed around its ground and points.

**Path art (Koopatlas's associate layers).** The tiles and doodads Koopatlas shows when a path opens now appear when that path opens in Deluxe too.
- Each piece becomes an image set to "Appears when path RW101F036 opens".
- On export, every path's art is one quad with that path's route material (`mt<path>a`) and a fade-only reveal mask. The game's own MaskRouteAnimation keeps it hidden until the path opens, then fades it in.
- Art on paths that nothing opens is painted into the picture, since it would never show.
- Your own images can be path art too: **Appears when** in the image panel.
- **Reveal preview** (Paths row) fades every path's art in, in play-through order. **Walk test** reveals each path's art as Mario reaches it.
- Yoshi's Island in World 1: 3 pieces fade in, 29 are open from the start. World 1 keeps clears for 1-1 to 1-5 only, so the paths that later levels open are open from the start.

**Map changes.** Nodes that jump to another Koopatlas map are named after where they lead ("Koopatlas: to Goldwood (Goldwood Forest, Newer world A)"); the import lists them.
- In Deluxe a world leads on to the next from its castle (Castle leads on to the next world). Travel between two imported maps is not made automatically.

**Right-click a point > Change to (World N):** what this world still needs (castle, W1S0...), the levels the game keeps clears for, the other levels (marked: their exits open nothing), the specials (tower, castle, ghost house, Toad house, airship, cannon, link), path stop or bend.
- Points whose course file is missing are greyed out.
- The point is renamed: its roads and unlocks follow. One undo step.
- **Mark as** puts the flags the world needs on it (map enemy starts, map Yoshi).

## What's new in 0.15.0: Koopatlas worlds in Deluxe, keyframed images, world map music
**Import Koopatlas…** (World Maker toolbar) turns a Koopatlas world map (`.kpmap`) into a Deluxe world. Koopatlas is the world map editor of New Super Mario Bros. Wii mods such as Newer. Only its map data is read; nothing of the Wii mod goes into the game.
- **The picture:** the tile and doodad layers are drawn into one ground picture, framed around the map's points.
  - Tile layers need Koopatlas's `Tilesets` folder (the `.arc` files); choose it in the import dialog. Without it, those tiles are missing (the importer says which).
  - Doodads are kept inside the `.kpmap`, so they always come across.
- **Moving doodads** (Loop / Reversible Loop keyframes) become moving images with the same keyframes ("Keyframes (Koopatlas)"): X/Y position, angle, X/Y scale, and opacity in the editor.
  - On export they become `CS_W<n>_Synchro` bone keys, like the other moving images.
  - Images sharing a picture share one texture, sized for how big they appear: Goldwood Forest (46 moving doodads) exports at 4.1 MB, Starry Skies (89) at 4.9 MB.
- **Nodes:** levels are matched by course number (1-1 -> W101, towers W1T0, castles W1C0...). Walk-through nodes become bends; other stops become path stops; the first stop without a level becomes the start.
  - Only names the game accepts at a path's end are used (the last character must be a digit, 0x60181FC0).
  - Courses with no file in the game become path stops, because entering a missing course crashes the game.
  - "Give levels the courses the game keeps clears for" (on by default) moves levels onto World N's tracked courses (World 1: 1-1 to 1-5), so beating them opens their paths.
- **Paths:** paths become routes with their action. Walk / sand / snow / water, jump, ladder (left/right), swim and fall map to Deluxe's actions.
  - Where Koopatlas lets two roads leave a node the same way, one gets a short bend, because the game keeps one road per direction (0x60178558).
- **Unlocks:** the level conditions become "opens when" on each path. AND becomes OR (the game has no AND); star-coin conditions open the path from the start. Both are listed in the import notes.
- **Music:** the world's music track becomes the map's Music choice when it is a Deluxe one (1-9).
- **Tested on Newer's maps:** Goldwood Forest, Starry Skies, Yoshi's Island and the world select import with no errors in the checks; Goldwood and Starry Skies also export. Fullmap (307 nodes, 290 paths) is over the game's 256 routes / 128 path stops and needs trimming.

**Music (World Maker > Map > Music):** pick which world's map music this world plays.
- Deluxe's world map music is one song with a part per world (nsmbud-reverse F0336). Koopatlas DX writes the choice as a hidden point `Fm01`-`Fm09`.
- The framework's new map music hook (`code/framework/map_music.cpp`, hooking `0x6080D540`) plays that part. Without the hook, the game plays its own music.
- It compiles in the framework build; it has not been tried in game yet.

**Keyframed images** work for your own images too: the "Keyframes (Koopatlas)" animation reads a sticker's `tracks`. Images can also have their own height and turn (`h`, `angle`), drawn in the editor and on export.

## What's new in 0.14.2: an easier World Maker
- **Straight paths:** the Route tool draws straight lines between your clicks by default, with a bend at every click. Follow the road is now an option, off by default.
- **Right-click the map** (any tool) to add a level, path stop, bend, tower, castle, ghost house, Toad house, airship, pipe end or link there. With Connect on, it is joined to the selected point.
  - Also from that menu: "Draw a path from here", and "Add the points the game needs here".
  - Right-click a point to draw a path from it, select it or delete it.
- **Fine moves:**
  - Alt-drag moves a point at a quarter of the mouse's speed, with no snapping. Ctrl-drag still turns off snapping.
  - The arrow keys nudge the selected point: half a tile; Shift for 2 tiles; Alt for 1/20 tile.
- **The toolbar's Action changes the selected path.** Selecting a path also shows its action there.
- **Properties on the right:** in the World Maker, the right-hand dock shows the selected point or path ("Point / path") and the Checks dock shows the maker's checks.
  - A point shows its name, point action, flags and camera, plus why the game needs it. The raw game-table columns are folded under "Game table (advanced)".
  - A path shows its action, Curve left / Curve right / Straighten and "Opens when…".
- **Needed points are easy to see:**
  - They are placed in a column where you are looking.
  - Each is labelled with what it is for (map music / cannon, map enemy starts here (W1Item), map Yoshi waits here (balloon1)).
  - They pulse for a few seconds, and the first is selected.
- **Tidier layout:**
  - The maker's side column is now tabs: Map, Your art, 3D scenery.
  - The main editor's World, Worlds and Events panels step aside while the World Maker is shown, and come back when you leave it.
  - The hint for the current tool sits under the Paths row.
- `samples/SMWsample.kdxmap`: its six needed points are now a labelled column at the top left of the map.

## What's new in 0.14.1: drawing paths in the World Maker
**Why you couldn't add routes:** the World Maker's tools were on one long toolbar row. Point, Route, Point kind and Connect were hidden behind its `»` overflow button. The main toolbar's + Level / + Stop / + Point / + Bend also did nothing in the World Maker tab.
- The tools for points and paths now have their own **Paths** row: Point, Route, Follow the road, Add needed points, Action brush, Select, Point kind, Connect, + Level, Action and Walk test. A hint line under the row explains the current tool.
- The main toolbar's + Level / + Stop / + Point / + Bend and Connect now drive the World Maker when it is shown.

**The Route tool (P) now draws a whole path:**
1. Click a point to start from, or click the map. The first point becomes the start if the map has none, otherwise a point of the Point kind.
2. Click along the road: each click adds a bend.
   - **Follow the road** (on by default) makes the path between clicks keep to the road in your picture, or in the painted tiles, adding the bends it needs. A dashed preview shows the way before you click.
3. To end the path: click a point, double-click (a new point there), or right-click / Enter (the last click becomes the point).
   - Shift-click ends at a new point and keeps drawing from it.
   - Backspace takes back one click; Esc cancels.
4. To branch, click an existing road while not drawing: a path stop goes into that road and the new path starts there. Unlocks of the split road are updated.
- With Connect on, the level before a new level or stop opens it, as with the Point tool.
- Each path is one undo step. Bends that would make a road leave a point twice the same way (the game keeps one road per direction) are dropped.
- Ladders, bridges and jumps: give a segment its action with the Action brush (A). Walk test (T) shows climbing and hops.

**Add needed points:** one click places every point this world needs so the game does not crash. They go in a row: move them where you like and join them with the Route tool. One undo step.
- The start (W1M0) and the castle (W1C0), when it leads on to World 2.
- The map-music point (W1S0).
- The points World 1's map enemies start on: path stops flagged W1Item, W1PakkunA and balloon1.

The export still adds any missing one hidden, so a world never crashes for lack of them.

`samples/SMWsample.kdxmap` now has these six points (no routes), ready for you to place.

## What's new in 0.14.0: images move in the game, transparency, resize handles (phase 5)
**Moving images now move in the game.** Every world loads a decoration model named `CS_W<n>_Synchro` and loops the bone animation of the same name. Vanilla World 1 uses it to sway its palm leaves (nsmbud-reverse F0335). On export, each moving image becomes its own quad in that model, on bones of its own:
- **GIF / PNG strip**: one quad per frame (up to 24). Each is shown only during its frame, timed from the GIF's own delays.
- **Bob, sway, spin, drift, pulse**: bone keys that follow the editor's motion. All images share one loop, picked so each one runs a whole number of cycles within 5% of its own speed.
- Images keep their **transparency** in the game (alpha cut-out, like the palm leaves). Still images are painted into the map picture as before.
- A test map with a 4-frame GIF and a drifting cloud came to 3.7 MB.

**Transparency:**
- **Picture over the tiles**: a ground picture with transparent parts (PNG, WebP, GIF) can lie on top of the tiles. Paint sea or sky with tiles and draw only your islands.
- **Raise islands from the picture's shape**: every tile where the picture is solid becomes an island at the Islands tool's tier, cliffs included. Undo works.

**Scaling images:** the selected image has corner handles. Drag one with the Select tool to resize the image in proportion (the Size field still works). Undo works.

**Not yet tested in game:** the moving images. The model, bones and looping animation match the vanilla ones and read back correctly in Koopatlas DX's own animation reader.

## What's new in 0.13.0 — Koopatlas-style 2D worlds: your own art, animated images, raised islands (phase 5)
Modelled on the *Custom World Maps* video: painted islands on cliffs, thick orange roads, palms, houses, ships and clouds. In the game a World Maker map is a flat world seen from above, so everything here ends up as its picture.

**A process you can follow (World Maker, 2D map):**
1. **Draw your world** in any art program (or start from tiles). Click **Picture ground…** in *Your art* and pick it (PNG, JPG, WebP, BMP).
   - Choose **Yes** to give the map your picture's proportions.
   - Tiles you paint afterwards go on top (roads, rims, details), so you can mix both.
2. **Raise islands** (optional): choose the **Islands** tool (T), set the tier (1-3), choose the cliff look (grassland, desert, snow, jungle, lava, castle, sky) and paint.
   - Painted areas become plateaus with cliff walls, the picture still on top. Shift-drag lowers.
   - Points placed there stand on the plateau.
3. **Put your points and roads** on top as before (Point and Route tools, Connect, + Level). Unlock with **Opens when…**.
4. **Add animated images**: **+ Image / GIF…** puts trees, ships, clouds, waterfalls or stars on the map. Drag them with the Select tool and set their size.
   - A **GIF** plays its own frames. A **PNG strip** plays N frames side by side ("Frames in strip").
   - Any image can **bob**, **sway**, **spin**, **drift** or **pulse**, with its own speed and amount. It plays live in the editor.
5. **Check and Export to game…**. The picture, tiles and images are baked into the map. Island cliffs are added as their own shapes, sharing the map's texture so the archive stays small (a 48×27 test map came to 3.9 MB).

**In the game the images show their first frame for now.** Moving images need one more engine piece (a texture animation), see the plan below. Everything else here works in the export.

**How the game animates a world** (nsmbud-reverse F0334): the game loops texture-scroll animations named after the world, `CS_W3_fts` on World 3's water and `CS_W3_Ocean_fts` on its ocean. It also plays decoration models by fixed names (`CS_W<n>_Ocean`, `_CloudBG`, `_Aurora`, `_Quicksand`, `_Doku`, `_Magma`, `_Universe`, `_Fog`, `_Castle`, `_Wall`, `_Synchro`). The plan for moving images:
- each image becomes a quad with its own material;
- a looping `CS_W<n>_fts` steps its texture through the frames (GIF / strip), or shifts and rotates it inside a transparent margin (bob, sway, spin, drift).

That uses only the game's own machinery.

## What's new in 0.12.0 — secret goals with Yaz0moto, junctions and stick directions, camera arrows, "Opens when" (phases 4 and 5)
- **Secret goals, checked in the level itself** (phase 4, with [Yaz0moto](https://github.com/Steammaker212/Yaz0moto)'s sprite data).
  - Koopatlas DX now reads each level's goal poles: Goal Pole 31 (nybble 5: regular / secret exit), Painted Goal Pole 503, and NSLU 630 / 631. In the vanilla game, 12 levels have a secret goal: 1-2, 2-4, 3-T, 4-T, 5-2, 5-4, 5-5, 5-T, 6-5, 6-T2, 7-T and 8-1 (nsmbud-reverse F0333).
  - **Error** when a point's secret exit opens paths but its level has no secret goal: the exit can never be found. Castles, airships and Bowser levels are exempt, since they end without a goal pole.
  - **Warning** when a level has a secret goal but its secret exit opens nothing.
  - The point panel shows "Level goals (1-2.sarc): 1 regular, 1 secret", plus an **Open the level in Yaz0moto…** button.
    - For a level from the game, it first copies the level to a working file (`~/KoopatlasDX levels`), opens that in Yaz0moto, and export copies it into the mod. The game's own file is never touched.
    - In Yaz0moto, give a Goal Pole **Secret Exit** and save; the checks read it back.
  - Yaz0moto is found next to Koopatlas DX's folder, or asked for once.
- **Junctions (three or four roads) and stick directions.**
  - The point panel lists which road each stick direction takes ("Stick: → F041 · ↓ F03c · ← F051"), red when two roads share a direction (the game keeps only one).
  - **Stick directions…** (point panel or right-click) opens a compass: pick the road for ↑ → ↓ ←. Koopatlas makes that road leave that way by adding (or moving) a short bend next to the point. The point itself doesn't move. One undo step.
  - **Branch to another point (junction)…**: click the target. The new road leaves, and arrives, in a free direction, the one closest to where it goes.
  - The 3D view draws a compass at the selected point: cyan arms for roads, orange for a clash, grey for free directions.
- **Camera arrows.** The selected point's camera (where it looks: point + offset) has its own X/Y/Z arrows. Drag one to move the camera along that axis (Ctrl snaps to 10). The nearer arrow wins where they overlap with the point's.
  - When the camera is on the point, the arrows are lifted above it with a stem down, so you can grab them. Offset 0 means the camera looks at Mario; 39 of World 1's 72 vanilla points use it, so new points keep 0.
- **"Opens when…"** (phase 5, Koopatlas-style unlock criteria), in the main editor's route panel and the World Maker.
  - For each unlock path a road is on, type who opens it: `1-2`, `1-2 secret OR 1-3`, `W102`, `1-21` (tower). Koopatlas writes the point table for you (Opens paths / Opens points of each exit). Undo works.
  - **AND** is refused with the reason: the game opens a path when any one of its openers is beaten (`RouteMgr::checkMovable`). Chain paths instead.
- Screenshots are in `Next Update/Phase 4 screenshots`. Not tested in game yet.

## What's new in 0.11.0 — secret exits and the levels the game remembers (phase 4, part 1)
- **The game only remembers some levels.** The save keeps a "cleared" flag only for the courses it has a slot for: 123 for normal exits and 82 for secret exits, over the nine worlds. This comes from the game's slot mapper, translated to Python (nsmbud-reverse F0332).
  - A level without a slot is never recorded as cleared, so the paths it opens **never open**. World 1 tracks only W101-W105, so a new **W106 can't work natively**. This is what would have happened to the W106 in your test after the missing level file.
  - Checks now say so as an error naming the paths that stay shut and the level numbers the world does track (and which are free).
  - The NSMBW port's W106 (no W1X0) is a warning instead: it works with the Koopatlas DX overlay hook that lends it W1X0's slot.
  - **+ Level** now picks a free tracked number first.
- **Secret exits tracked or not.** Secret exits are tracked only for normal levels and the S/G/T/C/A/B kinds (never Toad houses or enemy courses).
  - A point whose secret exit opens paths it can't record is an error.
  - The point panel now shows "Save: normal exit tracked ✓ · secret exit tracked ✓", or in red what isn't.
- **Secret roads look different.** Roads that only a secret exit opens have a pink outline in the 3D view (W1: the road and rainbow from W102's secret goal to W1S0).
- The exit pickers (normal and secret **+ Unlock next point…**, unlock cards) were already in place since 0.9.11.
- Screenshots are in `Next Update/Phase 4 screenshots`. Not tested in game yet.

## What's new in 0.10.3 — Mario stays on the ground; no false bone errors; levels for new level points
- **Roads that go through the ground** (in-game report: Toad sunk to the waist on a plateau while the editor's road looked fine).
  - The game moves Mario in a **straight line** between points and bends, at their heights; nothing keeps him on the ground in between. A road over a hill or a domed plateau goes through it in game.
  - Checks now flag every road that dips more than 15 units under the ground. Climbs and jumps are left out. Only ground facing up and at most 80 units above the road counts, so bridges, arches and tunnel roofs don't trigger it.
  - These roads are drawn **red** in the 3D view.
  - Fix one by double-clicking its check, or with right-click > **Follow the ground**: bends are added on the ground at the deepest dips until every piece stays within 4 units. One undo step.
  - Calibrated on the vanilla worlds: no vanilla road dips more than 14 units, so they show no warnings.
  - **Insert a point** and the **landing stops** after ladders are now put on the ground (they were placed halfway between two points, which on a plateau could be inside it). This only applies to roads, not climbs, and only while *Snap moved points to the terrain* is on.
- **No more "has no path bone" / "no bone named …" errors for things you just added.** The export builds the bones of new points, bends and paths (`kdx-bfres marker`), but the checks only looked at the model as opened. They now check the map-point model as the export will build it, with its bone order and positions. A path that would steal another path's point is still an error.
  - This also means the direction and climb checks now see new points.
- **Levels for new level points.** A new level (e.g. W106) with no `Course/1-6.sarc` would crash the game when entered. The check now has a line below it: double-click **to choose a level** (any `.sarc`, for example one of the game's own or your own). Export copies it into the mod as `Course/1-6.sarc`. Undo works.
- Not tested in game yet.

## What's new in 0.10.2 — preview before export, appear animations for your own models, a keyframe editor (phase 3)
- **▶ Preview works on animations that are only added on export** (new or copied appear animations), in the Events panel and on unlock cards.
  - Koopatlas DX builds the world in a temp copy, exactly as the export will. The first time takes about 3 s; after that it's instant until you change something.
  - The 3D view plays the animation from that build: the pipe really leaves the ground model and drops in. About 1.5 s after the end, the world's own models come back.
  - While it plays, the view uses the simple renderer, even with game shaders on.
  - If the object is off screen, the view moves to it.
- **Objects you added from DAE/FBX/OBJ (World models) can appear when a path opens.** They are listed first in **+ New appear animation…**, as "➕ name (added from file)".
  - On export each such object keeps its own pieces: it isn't merged with other objects that share a texture. Those pieces then move into the path's model.
  - If you delete the object later, export stops and names the appear animation that needs it.
  - Export order is now: map points → model edits → copied → new appear animations.
- **Keyframe editor.** Choose **Custom — your own keyframes** in the effect list and click **Keyframes…**, or select a new appear animation in the Events panel and click **Keyframes…**.
  - A timeline with three rows: **Move**, **Turn** (degrees) and **Scale**. Each key is a diamond.
    - Click a diamond to edit its frame and X/Y/Z, or drag it to retime it.
    - Double-click a row, or use **+ Key at playhead**, to add a key.
  - Under the rows, the selected row's curves are drawn: X red, Y green, Z blue.
  - Each row is **Linear** or **Smooth** (eases in and out of every key). Scale can be kept even with **Same X/Y/Z**.
  - **Start from** a preset (rise, drop, pop, grow) to adjust it.
  - **▶ Preview in 3D** builds and plays your keys without closing the editor.
  - Frame 0 is the pose the object waits in until its path opens (scale 0 = hidden). Editing is undoable.
- Engine (`kdx-bfres routemake`):
  - takes rotate keys (degrees, Euler XYZ) and a linear/smooth choice per channel;
  - writes **cubic** curves, the kind the game's own bone animations use, in their order (scale, rotate, translate).
  - The rebuilt engine round-trips vanilla W1 and W7 byte for byte, the same as the previous build.
- **How the game does its world events** (nsmbud-reverse F0328-F0331):
  - **W6 traps**: a road between two `W6trapA` points (red blocks) is blocked while the trap state is 0, and between two `W6trapB` points (blue) while it's 1. The toggle plays the skeletal animation `trapA` or `trapB` of `CS_W6a_Trap`. W3/W4 trap flags block too, but no animation plays for them.
  - **Switches**: `switchA`/`switchB` stop points share one switch state. Stepping on a live one flips it (the ? switch event). The flag help now explains both.
  - **W8a's dark cloud**: a fixed W8a actor plays `CS_W8a_DarkCloud`, `_Demo1`, `_Demo1After` and `_Demo2`, the cloud background demos, and dims the light.
  - **The camera when paths open**: it centres between the opened path's two points, then returns to Mario. Any new path with an appear animation gets this automatically, so keep the object near its path.
- Checked with the test suites, plus the new previews and editor in the window; screenshots are in `Next Update/Phase 3 screenshots`. **Not tested in game yet.**

## What's new in 0.10.1 — Mario stops after climbing; make anything appear when a path opens (phase 3)
- **Landing stops**. A two-way ladder ends on a pass-through point (it has to, or Mario couldn't choose the ladder from there), so after climbing he kept walking along the next road. Now a stop is put just past the top: Mario climbs, stops, and from there can walk back down or carry on.
  - It's added automatically when you choose **Two-way**: "Mario stops after climbing" is ticked by default in the ladder dialog.
  - Right-click a ladder for *Stop Mario after climbing (add landing stop)*.
  - At the bottom a landing is only added when the next point wouldn't stop him anyway.
- **+ New appear animation…** (Events panel): make any object of the world's ground model appear when a path opens.
  - Pick the path, tick the objects (searchable list of the ground model's pieces) and choose an effect: **Rise** out of the ground, **Drop** in from above with a bounce, **Pop** up from nothing with an overshoot, or **Grow** up. Set the length and height.
  - The objects stay anchored at their bottom centre.
  - On export the new engine command `kdx-bfres routemake` moves those shapes into a model named like the path, with bones `r<path>` ← `kdx_pivot` ← `kdx_appear` ← `kdx_inner` so the vertices don't change. It writes a skeletal animation of the same name whose first frame is the hidden pose. Undo and remove work.
  - Checked with our own animation reader: hidden at frame 0, full size at the end. The engine still round-trips vanilla W1/W7 byte for byte. **Not tested in game yet.**
- Found while doing it: the path roads painted on the ground use materials named `mt<path>a` (e.g. `mtRW103W104a`). That's the *mask* route animation (`"%s%c"` with `'a'`) that draws a path in as it opens.

## What's new in 0.10.0 — events and animations, part 1 (next update, phase 3)
- **How the game shows paths opening** (found in Deluxe's data and code, nsmbud-reverse F0326):
  - A model named exactly like an unlock path (`RW103W104`, as written in a point's *Opens paths*) holds the scenery for that path. Its root bone is `r` + the name without the R.
  - When the path opens, the game plays that model's skeletal animation of the same name once: W1's mushroom steps rise between 1-3 and 1-4. An optional `<path>_fts` scrolls its textures and `<path>_fcl` animates its colours. `CourseSelectMapMgr::initRouteAnimCore_` sets the skeletal, colour, texture and mask animations by the path's name.
  - The "Appear animations" columns are empty in every vanilla world.
  - Other animations are ambient loops (`CS_W<n>_Synchro`, W4's aurora) or event animations (W6 `trapA`/`trapB`, W8a's dark-cloud demos).
- **Events and animations panel** (beside Checks): every path's appear animation with who opens it (point, normal or secret exit), its length and texture/colour extras, plus problems (a model with no animation, a path no point of this map opens). It also lists the world's ambient and event animations. Double-click or **▶ Preview** plays one in the 3D view: only that model moves, from its start to its end.
- **Unlock cards show what opens**: a path card with an appear animation says "🎬 178 frames" and has a ▶ button.
- **Reuse a vanilla appear animation on your own path**: select one and choose **Copy to a new path…**. Pick the new path (for example the one to a level you just added); it's moved by the offset between the two paths' first points, which you can adjust.
  - On export, the model, its skeletal animation and its texture/colour animations are copied under the new path's name (new engine command `kdx-bfres routecopy`; the root bone is renamed and moved in both the model and the animation). Undo works.
  - The engine still round-trips vanilla W1 and W7 byte for byte.
- Not tested in game yet.

## What's new in 0.9.12 — ladders: choose one-way or two-way; undo fix
- Turning a road into a ladder or vine now asks **Two-way** (recommended: adds the pass-through points and makes it vertical, like vanilla, in one undo step) or **One-way** (kept as drawn: Mario only uses it walking on through its pass-through end). Cancel keeps the road. The one-way warning now says it's fine if that's what you want.
- Fix climb warns when a new pass-through point's road leaves a point the same way as another road.
- Fixed: undoing a new level left its camera (MarioOffset) record behind, so export reported "Records with no point". Undo now removes it and redo puts it back.

## What's new in 0.9.11 — add levels and points anywhere, two-way ladders, move arrows, readable unlocks (phase 2)
- **Add levels and points in the main window**: the toolbar has **+ Level**, **+ Stop**, **+ Point** (pass-through) and **+ Bend**. Click one, then click the map (3D view or 2D map) where it goes.
  - a ring and a dashed line preview where it lands;
  - it gets the next free name (W106, F900, K900…);
  - with **Connect** on, it gets a road from the selected point, and a new level or stop is unlocked by the level before it (added to that level's *Opens points* and *Opens paths*);
  - the status bar says which level file the new level needs (e.g. `Course/1-6.sarc`, *not in the game yet: make it with a level editor*). It also warns if the new road leaves the previous point the same way as another road.
  - right-click empty ground to add any kind right there; right-click a point for *+ Level after…*, *Road to another point…* and *Delete* (for points you added); right-click a road for **Insert a point in the middle**.
  - On export, the map-point model is rebuilt with a new engine command (`kdx-bfres marker`). Every game bone and marker stays where it was. New points get a bone and a vanilla marker (a level icon for W, a path dot for F), new paths get their path bone, and the game's bone order is kept except for the paths you changed. It round-trips vanilla W1 and W7 byte for byte, the same as the previous engine build.
- **Ladders that work both ways (Fix climb)**. Found in Deluxe's code: the stick only gives the four compass directions (`getDirFromKeyMask_` 0x601D8424). A vertical route uses the up/down directions, which no button gives, so Mario can never *choose* a ladder at a stop. He only climbs by walking through it. Every vanilla ladder runs between two plain F points with exactly two roads each (W2 Fc31-F032, W4 Fc01-Fj02, W6 Fc01-Fc02…). That's why your ladder from W105 only worked one way, coming down from the other side.
  - A new check flags climbs that start or end at a stop. **Fix climb** (route panel or right-click) adds the pass-through points and straightens the ladder, in one undo step. No vanilla climb is flagged.
- **Move arrows**: the selected point shows X (red), Y (green) and Z (blue) arrows in the 3D view. Drag an arrow to move along that axis only; Ctrl snaps to 10 units. Dragging the point itself still moves it freely.
- **Opens points / Opens paths made readable**: under the game values, each entry is a card.
  - Points: "W1K2 · Toad house".
  - Paths: "W105 → F008 (Path stop) · road of 2 pieces". Hover a path card to light up just that road.
  - Broken entries show in red ("no road joins W105 and W1K2"); × removes an entry. The raw game values stay editable.
- **Flags double-checked against Deluxe**: the wiki is for the Wii U game, so every wiki-only meaning is now marked *not yet checked on Deluxe*. From Deluxe's own flag readers:
  - `1Up`, `Shuffle` and `Slot` pick the Toad house type (`getCourseInCourseNo`);
  - `merge` is read by the lift and rail code;
  - `egg` stops the map Yoshi following past that point (`checkBanningEggFlag`);
  - the W3 water spouts block movement (`RouteMgr::checkMovable`);
  - `board1` is read when Mario arrives at a stop;
  - `restart` is read by `updatePos_`.

## What's new in 0.9.10 — climbs that work, plain-language flags and points, quick paths, curved routes (phase 2)
- **Why ladders and vines didn't climb in game** (found in the game's code, nsmbud-reverse F0322-F0324):
  - Climbing is the walk with another animation: the NetWalk state runs the Walk code (vtable +0xAC = `0x601D2928`). The footsteps change with the action, as you heard; the movement follows the route.
  - A route gets the **up/down** direction slots only when **both** its x and z offsets are under 3 units (`calcConnect_` `0x601A3C64`). Every vanilla ladder and vine is perfectly vertical. A slanted ladder is filed as a north/south walk, and the climb speed is only applied on up/down (`initMove_` `0x601D59EC`).
  - A route that ends on a map enemy's point (W1's Piranha Plant on `Fb01`) is replaced by the enemy's ambush. In the test clip Toad jumps straight at the Piranha Plant.
  - KDX's direction-slot rule ignored z; it now matches the game (no vanilla map loses a link under the new rule).
- **Climb checks and Make vertical**: the checks warn about slanted climbs, climbs onto map-enemy points and climbs that barely rise. The route panel (and the route's right-click menu) has **Make vertical**, which moves the bend or flag point straight above or below the other end.
- **Flags in plain words** (`kdx/flaghelp.py`): every flag the game knows has a name ("Stop here", "Ambush restart", "W1 Piranha Plant"), a group (Walking, Camera, Pipes and links, Map enemies, Yoshi and rides, Events and scenery), what it does, what breaks if it's misused, and how sure we are (traced in code / from vanilla maps / unknown). Sources: the game's code, vanilla maps and the NSMBU modding wiki.
  - The **Flags dialog** has a search box, a description pane, the point's flags summed up in words, and it hides flags only another world reads (toggle to show them).
  - `fix` (on vanilla W1 F001) is not in the game's flag tables: the game ignores it, and KDX now says so.
- **Point panel made simpler** (`kdx/pointhelp.py`):
  - one sentence says what the point is ("Level 1-3 (Yoshi Hill). Mario stops here and can enter Course/1-3.sarc.");
  - flags are summed up in words under the field;
  - the exits are sections, "When the level is beaten" and "When the secret exit is found", with plain row names;
  - **+ Unlock next point…**: one click on the map adds the point, the path to it, and a road if there is none;
  - rarely used columns hide until they have content or you tick "Show all columns".
- **Quick paths and level plots in the World Maker**:
  - **Connect** (toolbar, on by default): a new point joins the selected one with a road of the current action. When it's a stop, the level before it opens it (point and path), all in one undo step. Place points one after another to draw a whole path.
  - **+ Level**: the Point tool set to the next free level (W?01-W?0f), so new levels are placed, joined and unlocked in one click.
  - the flag picker shows plain names, with **More flags…** opening the full dialog.
- **Curved routes**: right-click a route for **Curve left** / **Curve right**, which bends it through three bends (K points) on an arc, keeping its action. Drag the bends to shape it. Unlock paths keep working, since they run stop to stop through bends. **Straighten** removes the bends again.

## What's new in 0.9.9 — pipes, cannons and links (next update, phase 2)
- **Reverse engineered** (nsmbud-reverse `FindingsG/WORLDMAP_ROUTES.md`, F0317-F0321):
  - `dokan` is the world's **warp pipe**. All of them form one network: Mario picks any world he has reached and lands on its start, or on World 1's pipe island. Only the first `dokan` point of a map counts.
  - `dokan2` is World 1's **island pipe**. The warp to the island lands on the point named `Fs01`.
  - A **cannon** is a point named `W<n>S0`. Its normal exit opens a chain of paths into a later world, and Mario is shot along the first one (`W1S0` → `W3J0`).
  - `link1`-`link5` join a point of a world's a map to the same-flagged point of its b map (W5, W8). `balloon`/`bubble` are the map Yoshi's spots; `boarding`/`station`/`coaster` are the lift rides.
- **Point actions** (`kdx/pointactions.py`). The World Maker's point panel has a **Point action** bar with Warp pipe, Island pipe, Cannon, Link, Map Yoshi spot and Lift ride. Each is one undo step:
  - Warp pipe moves the `dokan` flag here (a world has one);
  - Cannon renames the point to `W<n>S0`, asks which world to shoot Mario to and writes the path it opens;
  - Link picks the next free `linkN`; Map Yoshi uses the flag of the world's slot;
  - actions the slot can't use are greyed out.
- **Seen in every view.** Pipes, cannons, links, Yoshi and lift points get an icon badge in the World Maker (2D and 3D) and in the main 3D view. A green dashed arc shows the warp pipe's hop to the island (`Fs01`); a yellow dashed arc shows each cannon's flight, labelled with its landing point.
- **Checks**: two warp pipes in one map, `dokan2` outside World 1, an island pipe with no `Fs01`, and a cannon that opens no path.
- **Fix**: the 2D maker can scroll to points outside the painted map (vanilla imports such as Yoshi's Island's `Fd01`).
- Pipes between arbitrary maps (Koopatlas style) are not something the game does: it only links the a/b halves of a world. They need framework code (phase 5).

## What's new in 0.9.8 — route actions: what the game does, and routes you can see (next update, phase 2)
- **Reverse engineered.** The second column of `routeWn.csv` is Mario's movement on that segment:
  - `ReadAction_` matches it against a table of **23 actions**;
  - it stores the player's animation type;
  - the player then walks, slides, climbs or jumps.

  Details are in nsmbud-reverse `FindingsG/WORLDMAP_ROUTES.md`, F0311-F0316.
  - **Walk**: road, dirt, sand, quicksand, stone, snow, ice, cloud, water, switch block, wood bridge, stone bridge. Each has its own footstep sound, and all but the stone bridge kick up dust.
  - **Slide**: slope, ice slope.
  - **Climb**: ladder, ladder left/right, rock ladder (and its left/right), rope ladder, vine. The left/right ones climb facing 45°.
  - **Jump**: an arc over a gap.
  - Six of the 23 are in the game but no vanilla world uses them: switch block, quicksand, water, ladder left, rope ladder, dirt. They work.
  - The route table has no third column the game reads, so new movements (swim, crawl, custom speed) need framework code.
- **Routes look like what they do**, in the World Maker (2D and 3D) and in the main 2D map and 3D view:
  - each action is drawn its own way: a coloured road band with footsteps, planks on bridges, chevrons on slides, rails and rungs on ladders, a twisting stem on vines, a dotted arc on jumps;
  - routes show direction arrows and an icon badge;
  - they glow when selected or hovered;
  - routes the checks report get a red dashed outline and a red dot.
- **Working with routes** (`kdx/routeactions.py`, `kdx/routeui.py`):
  - an **action palette**, a Mario Maker style grid of the 23 actions in groups (Ground, Sky and water, Bridges, Slopes, Climb, Jump), each icon's tooltip saying what the game does;
  - the World Maker's toolbar **Action** button sets the action for new routes;
  - the selected route's panel has the same palette;
  - the **Action brush** (A) paints the palette's action onto any route you click or drag across (one undo step), in 2D and 3D;
  - **right click a route** in any view for its action menu (with icons), plus Select, Use as brush and Delete in the World Maker;
  - **click a route** in the main 3D view or 2D map to select it (its properties show, with icons);
  - the **walk test** moves at each action's speed: it climbs slowly with a wobble, slides fast, and jumps in an arc.

`Next Update/Phase 2 screenshots` shows the new looks.

## What's new in 0.9.7 — a polished window, a real gizmo, FBX export (next update, phase 1)
- **The look**:
  - a dark or light theme (*View > Theme*);
  - a toolbar with icons (Open, Export, Undo/Redo, 2D map / 3D view / World Maker, Frame, Models, Checks);
  - styled panels, tabs, menus and fields.
- **Panels**:
  - *View > Panels* shows or hides each panel (Worlds, World, Properties, Checks and the new **Log**, Ctrl+Shift+L), with *Reset the layout*;
  - the window's size and panel layout are remembered between runs.
- **Status bar**:
  - the open world and its point and route counts;
  - a **world size meter**: the archive's unpacked size against the size known to boot. It turns amber past it and red past the largest vanilla world.
- **Gizmo in every 3D editor** (World Maker 3D, *Tools > Edit this world's models*):
  - W move (arrows), E turn (rings), R scale (handles, and a centre square for even scaling);
  - drag a handle to work along that axis only;
  - X switches world/local axes;
  - **Snap** goes in steps of 10 units, 15° or 0.1, and Ctrl while dragging does the opposite.

  The old free drags and the typed position / turn / scale fields still work, and every change can be undone.
- **FBX export** (binary FBX 7.4, which Blender, Maya, 3ds Max and Switch Toolbox read; Blender reads binary only), `kdx/fbxwrite.py`:
  - *Edit this world's models* exports a model, the whole world or **one shape** as DAE, FBX or OBJ, textures included;
  - *Convert a 3D model* and the model editor's *Save as* write FBX too;
  - an exported World 1 imports into Blender 5.1 with all its 42 meshes and 62 textures.
- **Replace a shape** (*Edit this world's models > Replace…*): the game shape leaves the world, and your DAE/FBX/OBJ/Wii model takes its place with the shape's move, turn and scale. It is an added object you can move or edit further.
- **Exports report the world's size**:
  - every World Maker export says how big the world archive is unpacked;
  - it warns past about 13.7 MB, the size known to boot alongside the vanilla worlds;
  - Yoshi's Island test mod 12 at 28.6 MB crashed at boot because World 5's archive no longer fit.
- The painted ground of a 2D World Maker map is now stored in BC1 like the game's own ground, an eighth of RGBA8: a small 2D map was 14.5 MB unpacked.

## What's new in 0.9.6 — Yoshi's Island reworked: roads, scenery, world size
- **New roads, laid out like World 1's own**: one road on from each level, branches only at a level or a stop, wide
  rounded corners, no two roads side by side.
  - The meadow runs west to east: start (with the pipe just north), 1-1, 1-2. From 1-2 the road goes to the stop at the
    foot of the tower ramp, and the first house branches east from there.
  - 1-2's secret exit follows the south coast and joins the game's rainbow to the cannon.
  - Over the 1-3 mound the road comes down onto the upper plain at one junction: 1-4 north, 1-5 west (its house
    further west, the Piranha Plant on its way round), the baby Yoshi south on the plain's edge. Both ways meet at the
    merge stop, and World 1's ramp leads up to the castle.
  - The game keeps one link per direction at a point, so every road leaves a point in a direction of its own.
- **Scenery spread round the island, and nothing north of the castle**, where the walk on to World 2 goes:
  - the snow-capped peaks now rise from the sea east of the meadow, lower;
  - the Wii's hills are smaller, one or two to a place;
  - Soda Jungle's fan plant comes whole (the game draws it as two flat halves; 0.9.5 placed one half);
  - the Wii's fruit bushes are back (as on the Wii, apples and all);
  - the big log stump and the leaning small hills are gone, and the log posts are smaller.
- **Placement report**: the generator now notes anything that would hide a road or point. The camera looks north from
  350 up and 400 back, so a tall thing hides about 1.15 times its height of ground north of it.
- **World size** (the game holds every world's archive in memory: test mod 12 at 28.6 MB unpacked crashed at boot,
  because World 5's archive no longer fit):
  - export shares the vertices corners have in common;
  - parts drawn alike (same texture, mode and wrap) export as one shape, `export2d.drawn_alike`;
  - road edge masks are BC1, and the eggs share one texture per colour.

  Yoshi's Island is now 13.5 MB unpacked (vanilla World 1 11.0 MB; the NSMBW port, which boots, 13.6 MB).
- `assetkit.clusters` joins pieces whose boxes touch into one object: a plant drawn in halves, or a bush made of
  separate leaves and fruit.

`YoshiIsland_test_mod_14` is that world, exported. **Not yet tested in game.**

## What's new in 0.9.5 — Yoshi's Island, a World 1 built on the game's own
**`samples/models/make_yoshi_island_w1.py <out folder>`** builds the *Yoshi's Island* project (World 1, template
`CS_W1`). It needs the romfs (`--romfs` or `KDX_ROMFS`) and the NSMBW dump (`--nsmbw` or `KDX_NSMBW`).
- **The ground is World 1's own**: its grass, cliffs, pipes, flowers and giant mushroom. Left out are the acorn trees,
  the white rock range, the rings and the roads, so the island no longer reads as Acorn Plains.
- **The same levels in the same order on new roads**:
  - start, 1-1, 1-2;
  - 1-2 opens the first house, the tower and, by its secret exit, the cannon;
  - the tower halfway;
  - 1-3, which opens 1-4 and 1-5 at a junction (the Yoshi spot is beside it);
  - 1-5 opens the second house, and its road carries the Piranha Plant map enemy;
  - 1-4 and 1-5 meet below the castle.
- The castle, the cannon and the pipe island stay where World 1 has them, so the walks on to World 2 (and to World 3
  by the cannon) are the game's own, and a future World 2 joins on as the vanilla one does. The ramps up to the tower,
  1-3 and the castle are the ground's own, so those roads follow World 1's walks there.
- **Roads are built from the game's road texture along each path** (60 wide, bends rounded) and stay hidden until
  their path opens, as in 0.9.4. World 1's rainbows to the cannon and on from it, and the castle's road on to World 2,
  fade in when their paths open.
- **Boshi's tower and King Wart's castle**: World 1's tower in blue stone with a red spiked collar, and its castle with
  frog-green roofs and a gold crown on the main tower. (The game's `cobToride` and `cobCastle` are only their gates;
  the buildings are World 1's own geometry, so no other world changes.)
- **Scenery from the games, plus eggs made here**:
  - the Wii's World 1 hills as snow-capped pale-blue mountains behind the castle, small white-topped hills in the
    meadows, and a row of the Wii's spotted and wavy hills along the open west edge;
  - Sparkling Waters' palms;
  - Soda Jungle's fan plants (in sunnier greens), a log stump made into a grass-topped plateau, and its log posts;
  - Meringue Clouds' puffy clouds;
  - Yoshi eggs (white with big round spots, measured on the shell so they stay round) and Yoshi's egg house by the
    start;
  - a small volcano.

  Everything is a scenery object, so each one can be moved or deleted.
- **`kdx/assetkit.py`** cuts scenery out of game worlds: `native_parts` (NSMBUDX shapes), `wii_parts` (NSMBW
  materials) and `pieces`. A world draws every hill of a colour with one shape, so `pieces` splits such a shape into its
  objects, and a cap standing on a body stays with it. `centred` puts an object's foot at the origin.

`YoshiIsland_test_mod_12` is that world, exported. **Not yet tested in game.**

## What's new in 0.9.4 — paths stay hidden until they open
**NSMBW World 1** (re-port with *Tools > Port an NSMBW (Wii) world…*):
- **Paths appear as you open them, as on the Wii.** Both games hide a path with a mask texture
  (`texMaskOpaque` / `MaskOpaque`, the same image) that slides along the path's own mask UVs. The game finds
  the path's materials by name, `mt<route><a-h>`. The Wii keeps the hidden position in the material, and the game
  slides it open when the route opens (`MaskRouteAnimation`). The port now:
  - keeps the Wii's mask UVs and hidden position on each road;
  - names the materials after the port's routes (`mtRW103W1W0a` → `mtRW103W1S0a`);
  - exports them as copies of a vanilla path material.

  Bridges and stone paths open the same way.
- The **Goomba square's roads** fade in when 1-4 opens them (the Wii fades them in with a colour animation).
- The **cannon's plot** (W1S0, a secret point) no longer shows an empty rim: its rim appears with the path to it.
- The **flat flowers** are cut out again. The Wii cuts the transparent texels of their CMPR texture; 0.9.3 drew them
  opaque, with black boxes. The fruit stays opaque, as on the Wii.

- **Fixed after test mod 9: road pieces that came out dark.** The game's road normal map is BC5 SNORM, and the
  route shader reads its x/y as signed values, so a flat normal map stores 0, not grey 128. Grey tilted every road
  normal about 45° along its texture direction, and pieces facing one way were lit from behind.

Model files keep the mask with the road: a part's second UV set (`TEXCOORD` set 1) and a `KDX` extra
(`mask_offset`) on its material. Any part named `mt<route><a-h>` with a second UV set exports as a path that opens
with that route. A route no point opens is exported shown, so it can't stay hidden for good.

## What's new in 0.9.3 — level rims, the Wii's fruit and shading, the path to the castle
**NSMBW World 1** (re-port with *Tools > Port an NSMBW (Wii) world…*):
- **Gold rims around the level discs.** NSMBW's level node (`cobCourse`) is a coloured disc inside a gold rim.
  NSMBUDX's `cobCourse` actor is the same disc without the rim, and its worlds draw their rings in the world model. The
  port now stands the Wii's rim (`mtCoursePoint`) at every level point except the start, where vanilla World 1 has its
  rings. Each rim is a scenery object (*Level rim W101*…), so you can move or delete it.
- **The fruit keeps its green top.** A Wii material whose texture has alpha is only cut out when the Wii tests alpha
  or blends (`kdx.brres.Material.blend`, BP `0x41`). The roads blend, so they stay cut-outs. The fruit and one flower
  do neither: the Wii draws them opaque, and the fruit's alpha-0 rows hold its green calyx. Cut out, it showed a
  dark band.
- **The bush's leaves are shaded like the Wii's.** The Wii multiplies each vertex's grey colour in; NSMBUDX's world
  shader does not (its `_c0` is a mask). Materials whose pieces differ in shade (bush leaves, flowers, cliffs) get
  texture copies per shade level, one level per leaf or flower. Each material keeps its average brightness, and a
  single large mesh such as the ground keeps its look.
- **The path from 1-6 to the castle opens.** World 1 keeps clear flags for 5 normal courses only, so the port's sixth
  level never counted as cleared. The `nsmbw-w1` patch (`NsmbwW1_courseSlot`) stores 1-6 in the slot of W1X0
  (Peach's Castle), which the port leaves out.

Still not like the Wii: its lightmaps (`lm_01m`, `lm_02m`) are mapped by the camera's view of each normal and pull
about 20% toward the vertex colour. NSMBUDX lights the ported textures its own way, so very saturated textures (the
bush's `happa`, the road) stay brighter than on the Wii.

## What's new in 0.9.2 — the Goomba square, the Wii sky, moving the game's own shapes

**Edit a world's own shapes** (*Tools > Edit this world's models…*): click a shape in the 3D view (a red pipe, a tree)
to select it. The *Selected shape* box shows its position, turn and scale; drag it with Move / Turn / Scale or type
values. **Retexture…** gives that one shape its own texture (its material is copied, so other shapes using the texture
keep it); **Game texture** and **Reset** undo. The first click on a shape only selects it, so a click on the grass
never drags the ground. On export the model writer moves the shape's vertices in the file (normals and culling bounds
too) and keeps its own material and shader.

**NSMBW World 1, closer to the Wii** (re-port with *Tools > Port an NSMBW (Wii) world…*):
- The Goomba square's roads are back. They hang on NSMBW's `kuribo` bone, which the port took for the Goomba itself;
  its colour animation only fades them in (alpha 100 to 255) once 1-4 is cleared.
- World 1-4 opens every path of the square, each cut at its stops (`RFb00Fb01`, `RFb01Fb02`, `RFb02Fb03`,
  `RFb00Fb03`, `RFb03W106`, `RFb01W1K3`, `RFb02W105`). NSMBW's `RFb00W106` walked through the stop Fb03.
- The game keeps one neighbour per slot, and for neighbours lined up in x it picks the slot by height alone
  (`0x60178558`). Three NSMBW roads ran straight north–south and lost a link: W103 lost its Toad house, Fb02 lost
  Fb03, bend Kn0f lost W105. The port now moves such bends 4 units east. `kdx.rules.slot_losses` replays the game's
  slot build exactly.
- **Wii sky backdrop**: NSMBW's sky and far clouds stand 420 units behind the castle as a scenery object. In
  NSMBUDX all worlds share one 3D space, so World 2's desert showed where the Wii has sky. Move, scale or delete it like
  any object.

**Code for the port** lives in the framework repo, separate from the plain build:
`nsmbud-reverse/patches/nsmbw-w1` (`py -3 build.py` builds the whole framework plus these hooks):
- a guard for the path step that crashed at `main+0x1D8500`;
- a fix so Mario's walk loop stops at that point;
- World 1's map enemy is World 2's map Goomba instead of the Piranha Plant.

`NSMBW_W1_test_mod_3` includes that build.

## What's new in 0.9.1 — the Wii model ported properly, editing the game's own world models

From the first in-game test of NSMBW World 1 (`NSMBW_W1_test_mod_1`):
- **Fixed: paths showed as torn patches.** NSMBW's path materials have two UV sets: the reveal mask on set 0, the
  road on set 1. The Wii reader used set 0 for every texture. Each layer's UV set is now read from the material's XF
  texture-coordinate generators (display list + 0xE0, after BrawlCrate's `XFData`).
- **Fixed: World 1's mushrooms wore the Toad house's wood-ring texture.** NSMBW's `CS_W1` and `cobKinokoRed` both
  have a texture named `texKinokoRed`, written to the same folder. Textures are now named after their model too
  (`CS_W1__texKinokoRed.png`).
- **Texture wrap modes kept.** The wrap fields sit at +0x18/+0x1C of a texture reference (BrawlCrate
  `MDL0TextureRef`), not +0x14, which was reading an index.
  - NSMBW clamps its sky and mirrors its mushroom caps, flowers and bridges (half a texture, mirrored).
  - The wrap modes go into COLLADA (`wrap_s`/`wrap_t`) and the 3D views.
  - The exported world's samplers carry them too: the model writer sets them on each part's albedo sampler.
- **Toad houses stand up.** Their rest pose is sunk in the ground (`kinoko` at y −45). NSMBW raises them with
  `cobKinokoAppear` when they open. The Wii reader now reads CHR0 bone animations (all six track encodings, after
  BrawlCrate's `AnimationConverter`), and the port draws scenery at the end of its appear animation. The Star house
  borrows the red house's animation.
- **Fixed: Wii polygons bound to the wrong bone.** A polygon's node id is a matrix id, not a bone index (BrawlCrate
  `MDL0Object`). It only differed for later bones, such as World 1's mushroom lifts.
- **The crash after the Piranha Plant**: found at `main+0x1D8500` (`0x601D8500`) from the Ryujinx log.
  - Cause: a map walk stepping in a direction where its point has no neighbour (`Table6_getEntry` returns NULL).
  - Most likely trigger: a save that had been on vanilla World 1. Map positions and map-enemy state are saved by
    point index and direction.
  - **`CUSTOM-CODE-PLAN.md`** has a guard hook for your coder (plus a log line and an optional save clean-up).
  - Test the port with a new save.
- **Test mod: `NSMBW_W1_test_mod_2`**. **Not yet tested in game.**

**Editing the game's own world models** (*Tools > Edit this world's models…*, Ctrl+M, on an opened world):
- Every model and shape of the world, drawn with its textures in the same 3D editing view as the World Maker.
- **Untick a shape** to take it out of the world.
- **Replace a texture** with a PNG; save any texture as PNG.
- **Add objects** from FBX/DAE/OBJ/Wii models and place them by dragging or typing. They are written into the world's
  terrain model with copies of its own materials; the model editor works on them too.
- **Export** any model, or the whole world, as COLLADA with PNG textures.
- Edits are undoable and applied when the world is exported. The model writer's new `edit` command changes only
  what was edited; every other model stays as it was.

New checks in `tests/test_models.py` (66 now):
- the road's UV set and the wrap modes
- the CHR0 end pose and the risen Toad house
- per-model texture names
- editing World 1's own models end to end (shape removed, texture replaced, object added, others untouched, rules
  still pass)
- the world model editor itself

## What's new in 0.9 — FBX/DAE models, 3D world editing, model editing, NSMBW ports

- **FBX and COLLADA (.dae) models**, as well as OBJ, wherever a model is used (*3D ground model…*, scenery objects,
  *Tools > Convert a 3D model…*). Wii models (`.brres`, or a `.arc` holding one) open directly too.
  - FBX: binary and ASCII (FBX 7, 2011 or newer). Node transforms (translation, rotation with pre/post rotation and
    pivots, scale, geometric transforms) and the file's up axis are applied. Sizes come out as Blender showed them
    (FBX centimetres ÷ 100). Embedded textures are written to `%LOCALAPPDATA%\KoopatlasDX\cache`.
  - COLLADA: nested node transforms (matrix, translate, rotate, scale), `triangles`/`polylist`/`polygons`/strips/fans,
    `up_axis`, textures through sampler → surface → image, transparency, double-sided.
  - Checked against Blender 4.5's own FBX and COLLADA exports of the Yoshi Island sample: the same triangles, sizes,
    materials and textures as the OBJ, also with the model parented under a turned, scaled empty.
  - Writing: COLLADA and OBJ, with textures copied next to the file.
- **3D world editing** (World Maker, new **3D world** tab next to the 2D map):
  - the ground model and every scenery object drawn with their textures; faces culled as the game culls them
  - points and routes on top
  - click to select; drag to **Move** (over the ground, Shift: up and down), **Turn** (around the up axis, Shift: x,
    Ctrl: z) or **Scale**; drag points over the ground
  - *Stand on the ground* keeps moved objects and points on the ground model; *Lock the ground* stops it being moved
    by accident
  - right drag orbits, middle drag pans, wheel zooms, F frames, Delete removes, Ctrl+D duplicates
  - every change is one undo step
- **Scenery objects** (*3D scenery* panel): castles, houses, props from any model file, each with a name, position,
  turn and scale (typed or dragged). They are exported with the ground, one shape per material. Objects also work on
  painted 2D maps: the map stays the ground.
- **Model editor** (*Edit…* next to the ground model, *Edit model…* for an object), with a live preview. Per material:
  - show or hide it
  - how it is drawn: opaque, cut-out (alpha test) or see-through (blend)
  - its texture, or a plain colour
  - two-sided; faces turned round
  - click a face in the preview to find its material

  For the whole model: turn, scale, offset/position, and its file's up axis. Edits are stored in the map; the model
  file is never changed. *Save as DAE/OBJ…* writes the edited model as a new file and can switch the map to it.
- **Cut-out materials in game.** Exported parts drawn as cut-outs keep their alpha (RGBA8 textures) and copy a vanilla
  alpha-test material from the template (World 1: `mtKinokoRed`). The model writer now picks a donor material per
  render mode. See-through parts use the cut-out donor too: no template material is simple enough to copy.
- **Port an NSMBW world** (*Tools > Port an NSMBW (Wii) world…*). Pick your NSMBW dump (the folder with `WorldMap`), a
  world and an output folder; the map opens in the World Maker. **World 1 is ported**:
  - Read from NSMBW's own files: `CS_W1.arc` (the model, whose bones are the points and path bones, named like
    NSMBUDX's), `RouteInfo.arc` (`pointW1.csv` and `routeW1.csv`, in NSMBUDX's CSV layout with Shift-JIS route types),
    and the `cob*.arc` scenery placed at child bones: Bowser's castle, the tower, three Toad houses, the cannon,
    Peach's Castle.
  - The ground becomes a COLLADA model with PNG textures. Left out: sky dome, clouds, shadow decals and the Wii level
    markers (NSMBUDX draws its own), and the moving map actors (Goomba, airship, clouds). The mushroom lifts to the
    castle become an object.
  - Greyscale textures that the Wii coloured in its TEV (water, sky) are coloured with the material's TEV colour
    registers. Layouts from **BrawlCrate** (BrawlLib: MDL0 materials, TEV colour/konst blocks, texture SRTs).
  - Renamed for NSMBUDX:
    - start `W1S0` → `W1M0`
    - cannon `W1W0` → `W1S0`, NSMBUDX's secret point that warps to World 5, still opened by 1-3's secret exit
    - bends `K…` → `Kn00`… (NSMBW's `K072` is a bend on NSMBUDX's walk to World 2)
  - Flags NSMBUDX does not know are dropped (`demo1`, `demo2`, `kuribo2`); the Goomba stops carry `W1PakkunA`.
  - W104 also opens the Goomba square's paths, which NSMBW opens from its map-enemy code.
  - The points-unlocked column is filled from the paths each exit opens, as in NSMBUDX's tables.
  - Peach's Castle point and its walk (NSMBW's opening scene only) are left out; the castle model stays.
  - The rules pass (17/17 stops reachable). The export adds `W1Item`/`balloon1` hidden, as for any map. The castle
    leads on to World 2 and `W1S0` towards World 5, as in NSMBUDX.
  - **Test mod: `NSMBW_W1_test_mod_1`** (6.1 MB, vanilla World 1 is 6.5 MB). **Not yet tested in game.**
- **Fixed:** the way on to the next world dragged a template exit into the map when the template's table lacked one
  of the map's own points (World 1 has no `W1K3` row, so `RW104W1K3` passed for a path to another world).
- **Fixed:** moving a point that has its own 3D position (ported, or copied from a template) in the 2D map now moves it
  in the export too.
- New tests: `tests/test_models.py`, 50 checks:
  - FBX binary and ASCII written by an independent writer in the test: transforms, axes, units, embedded texture
  - COLLADA and OBJ round trips, COLLADA up axis and nested nodes
  - GX texture blocks (CMPR, RGB5A3, I8, IA8) decoded from hand-made bytes
  - model references and material edits; objects saved relative to the map
  - the NSMBW World 1 port and its export
  - the 3D world tab and the model editor (drawing, dragging, undo, edits)

**Known limitations (0.9):**
- Objects are static: NSMBW's door, flag and lift animations are not ported.
- Vertex colours are read and shown in the editors, but exported white (as before).
- Where World 2 joins the ported World 1 is set by the castle, as for every map. World 2 starts about 470 units
  behind the castle (vanilla: about 330), so the two maps share a strip of ground, as the vanilla ones do. **Check it
  in game.**

## What's new in 0.8 — 3D worlds, textures, animations

- **Custom 3D worlds from your own model.** In the World Maker, *Map > 3D ground model…* loads a **Wavefront OBJ**
  (with its MTL materials and textures), which almost every 3D program exports (Blender, Maya, 3ds Max, SketchUp…).
  It can be Y-up or Z-up.
  - The model is fitted into the map, shown from above under the grid, and saved with the map. Its path is stored
    relative to the `.kdxmap`, so you can move the folder.
  - Points stand on its surface. *Remove* goes back to painted tiles, and undo works.
  - The export writes **one terrain shape per material**, each with its own texture (or a colour swatch from the
    material's `Kd`), compressed to **BC1** like the game's own (up to 2048 px). Textures repeat (the copied ground
    material's samplers wrap), so tiled UVs work. Keep each material under 21,845 triangles (16-bit indices, the kind
    tested in game).
  - **Lighting as in the game**: the template world's lightmaps stay, and the baked light is set to vanilla grass
    brightness, made grey for the copied material's tint. (Test mod 10 replaced them with white and came out washed
    out, its beach pure white.)
  - **Faces must point outwards**: the game draws front faces only (counter-clockwise seen from outside). Inside-out
    triangles are turned round on loading (by the file's normals, or the whole model when it is inside out) and the
    World Maker says how many. Test mod 9 showed why: its island wound clockwise, so the game drew no grass, only the far
    cliff from the inside (a yellow arc over the sea).
  - Samples: `samples/Yoshi Island 3D.kdxmap` and `samples/Hill Island 3D.kdxmap`. Yoshi Island is a grassy island
    with soil cliffs in a foaming sea, Yoshi eggs, palms, bushes, smiley flowers, a warp pipe, rock pillars, Boshi's
    Castle behind the tower and King Wart's Castle by the castle point, and a bridge carrying the walk on to World 2.
    It is rebuilt by `samples/models/make_yoshi_island.py` (original art). It stays south of z = 400: after the export's
    shift, World 2's own ground starts there.
  - Tested: the textures come back block for block after the export, points stand on the surface, the game's rules
    pass, BfresLibrary reloads the file, exported ground faces up, the lighting values. **In game:** test mod 10 loaded
    and drew the island (the face fix works) but too bright. **Not yet tested in game:** `YoshiIsland3D_test_mod_11`.
- **Textures and materials in the 3D view.** Every world is drawn with its real textures and the game's render states:
  - opaque, alpha-tested ("mask") and translucent materials, in that order
  - path decals without flicker, and course markers cut out
  - faces culled per material as the game does, so an inside-out model looks broken here too, not only in game
  - ground shadows (World 1's acorn trees) dark, as in the game, instead of white rings
  - *View > Textures and materials* (X) switches to flat colours.
  - The new texture decoder (`kdx/texture.py`) reads all **1,193 textures** of the 12 world maps in 3.4 s. Every
    pixel is identical to Pillow's decoder, and the Tegra X1 deswizzle is identical to Yaz0loom's
    (`tests/test_textures.py`).
  - Formats: BC1, BC2, BC3, BC4, BC5 (unsigned and signed) and RGBA8, which covers everything the maps use.
- **Animations** (*View > Play animations*, Space): texture scrolling (water edges, rivers, clouds) and moving
  scenery (the `_Synchro` skeletal animations: flowers, bushes and trees bobbing).
  - The model writer decodes the animations (new command `kdx-bfres anims`), and `kdx/anim.py` evaluates the curves
    (cubic, linear, step, baked), texture SRTs and bone poses.
- **Ride-along camera preview** (*View > Ride along a path…*, Shift+R): Mario walks a path, or every path in turn,
  and the view follows the map camera.
  - Two readings of the camera table: "where the camera looks" (Mario + offset, from the map's fixed angle) and
    "where the camera is". Which one the game uses is not confirmed yet, so compare both with the game.
  - Two ways to blend between points (smooth, or hold until the next point), and an adjustable speed. Esc or a click
    stops it.
- The model writer is rebuilt from the BfresLibrary source now kept in `engine_src/BfresLibrary`. Before it was
  installed, the rebuild was checked: vanilla worlds come out byte-identical to the previous writer.
- New tests: `tests/test_render.py` (real OpenGL, off screen) checks textured drawing, animations and the ride-along.

**Known limitations:**
- Lighting is a single sun. The game's own shaders and baked lightmaps are not used, so colours are close but not
  exact. A few tree crowns show white edges where their alpha comes from another texture slot.
- Imported models are static ground: no skeletal animation of your own models yet, and no extra scenery objects
  (castles, houses) beyond the game's markers.

## What's new in 0.7.1

- **Walk test** (*Walk test* button or T, in the World Maker): a marker walks every path in the order a play-through
  opens them. Paths open from the start come first, then each course's paths once it is reached. The status bar names
  each path and what opens it. Click or press Esc to stop.
- **Add flag** (Selection panel): a list of flags for the selected point. **This world's needs come first**: its map
  enemies' start flags and its map Yoshi spot (W1: `W1Item`, `W1PakkunA`, `balloon1`), each with where it is placed or
  "not placed: hidden on export". Then the common flags (`stop`, `restart`, pipe ends, camera). Undo works as for any
  edit.
- **Red rings on problem points**: every point named in an error of the checks gets a red ring on the canvas. Hover
  over it to see the error. This makes direction clashes (such as V-shaped bends) and broken paths easy to find.

## What's new in 0.7

- **Fixed: the player could not move on an exported world.** The game does not get its walkable paths from the route
  table. When it builds a map (`0x60181FC0`), each path is a **bone of the map model** named `R<from><to>`, followed in
  bone order by the `F`/`K` bones it walks through. Every vanilla path has one, including `RW1M0W101` from the start
  point. 0.6 wrote none, so nothing could be walked.
  - Exports now write a path bone for every path, in vanilla's order: unlock paths first, then routes open from the
    start, joined into paths between stops. For World 1's own tables this rebuilds **all 17 of Nintendo's path bones
    exactly**: same names, same walks, same direction.
  - A path can only end at a point whose name ends in a digit (the game checks characters 4 and 8 of the bone name).
    New path stops and pipes skip names like `F00a`, and the checks flag paths that break the rule. They also flag a
    point on two paths' walks, more than 128 paths, and walks longer than 40 points.
  - The checks (and `wmtool check`) report a world with no path bones, and an unlock path without its bone, as errors.
- **Castle leads on to the next world** (*Map > Castle leads on to the next world*, on by default). Your castle gets
  the game's own exit (W1C0 opens `W201` and `RW1C0W2J0,RW2J0W2M0,RW2M0W201`). The vanilla walk to World 2's junction
  (`F191 … K19e`) is copied next to your castle, and `connectW2` moves with it so World 2 still lines up with the walk's
  far end. Untick it for a world that ends at its castle.
- **Terrain height (3D worlds)**: the **Height** tool (H) in the World Maker. Drag raises the ground, Shift-drag lowers
  it, Ctrl-drag flattens to the height where you pressed. *Size* sets the radius, *Height step* the amount per dab.
  The canvas shows hill shading, and the status bar the height under the mouse.
  - The export builds the terrain from it (one cell per tile, up to 128 per side, with normals from the slope).
    Points and bends stand on the ground.
  - Mario walks in straight lines between points, so put bends (K) on long slopes to keep him on the ground.
- **Point tables are written exactly the way Nintendo quotes them**: all twelve vanilla tables come back
  byte-identical. This applies to editing existing worlds too.
- **One neighbour per direction.** The game links each route's two points by direction (east/west, north/south, or
  an up/down pair when they are lined up in x), and a point keeps one neighbour per direction (`0x60178558`). A second
  route leaving the same way replaces the first. The checks now report this as an error; no vanilla map has one.
  - A V-shaped bend (both routes leaving it north) is the usual way to make one: use an L-shaped bend instead.
  - Test 4 had this at the castle (the tower route and the walk to World 2 both left west), so the castle lost its
    link to the tower. The castle link now picks a free side of your castle for the walk.
- **Fixed: the map froze when Mario walked back to the start point** (or, on a complete save, to the castle). Found
  with Ryujinx's GDB stub. The frozen game was running event 116, "Yoshi joins", stuck at its step 324, and Mario's
  update was paused. W1 has a map Yoshi, which waits on the point flagged `balloon1` (vanilla `Fys1`). With no such
  point it stayed on Mario's own point, so walking back there started the event, which never ends. Like the enemy
  flags, the Yoshi spots (W1 `balloon1`, W3 `balloon2`, W4 `bubble1`, W6a `bubble2`) are now required: the checks
  report a missing one, and the export adds it hidden and out of reach. To have a Yoshi on your map, put the flag on a
  reachable point. The freeze went away once Yoshi had joined, which is how the cause was narrowed down.
- **Tested in game (Ryujinx, 2026-10-27): `StarterIsland_test_mod_7` works.** It has every fix (path bones, Yoshi
  spot, castle link). Mario walks every path in both directions, the castle leads on to World 2, and walking back
  from World 2 to the castle works, all without freezes.

## What's new in 0.6.1

- **Fixed: exported worlds crashed the game after loading.** The first Starter Island test loaded in Ryujinx and then
  crashed at `main+0x1E7FB4`. When the W1 map chooses its music, the game looks up the point **`W1S0`** by name and
  reads it without checking that it exists (`0x601E7F30`), and Starter Island has no `W1S0`. Reading every caller of
  that lookup found the full list of names the game needs on each map:

  | Archive | Must have | Why |
  |---|---|---|
  | CS_W1 | point `W1S0`; flags `W1Item`, `W1PakkunA` | map music; W1's two map enemies |
  | CS_W2a | point `W2S0`; flags `W2EnemyA`-`C` | map music; map enemies |
  | CS_W3 | flags `W3EnemyA`, `B` | map enemies |
  | CS_W4 | point `W4G0`; flags `W4EnemyA`-`D` | ghost-house effect; map enemies |
  | CS_W5b | flags `W5bEnemy1`-`3` | map enemies |
  | CS_W6a | point `W6C0` | return to the castle |
  | CS_W7 | flags `ChorobonA`-`C` | map enemies |
  | CS_W8b | point `F001` | Bowser castle intro |

  A map enemy starts on the point carrying its flag. If no point has the flag, the game reads the enemy's position
  through a null pointer.
  - **The export now adds anything missing as a hidden placeholder**: a point with no marker and no route, 1000
    units under the start point. You can also place these points and flags yourself: a real `W1S0` with a route to
    it is a playable secret level (1-13), and a flagged path stop is where that enemy starts.
  - The checks list these placeholders, and the export dialog names them before you export.
- **The world-link bones are kept.** The export used to drop the template's `connectW<k>` / `GrandCentral` bones. The
  game uses them to place each world among the others, and without them it reads bone −1. They now stay where the
  vanilla map had them.
- **The World Maker knows which archive it replaces** (*Map > Replaces*: CS_W2a, CS_W5a or CS_W5b, …). The export
  dialog picks that template.
- **Mod folder at any level.** Choosing `…/atmosphere/contents/0100EA80032EA000/romfs` (or `atmosphere`, the title
  folder, or `Model`) used to nest a second `atmosphere/…` inside it, where the game never looks. Any of these now
  writes to the right `romfs/Model`.
- The World Maker canvas no longer shrinks to a sliver on small screens (clicks then picked the nearest point instead
  of placing a new one).
- Fixed an error logged when closing the window (`wrapped C/C++ object of type MainWindow has been deleted`).
- `wmtool check` (nsmbud-reverse) has the same new errors. On the crashing test map it now reports 3 errors (`W1S0`,
  `W1Item`, `W1PakkunA`); the fixed export and all 12 vanilla maps report none.
- **Test with a new save file.** A save that has been on the vanilla map remembers its position as a point index, and
  its path-stop records by name.

## What's new in 0.6

- **Export World Maker maps to the game** (*World Maker > Export to game…*). A map made in the World Maker (like the
  `Starter Island` sample) becomes a real world archive, `CS_W<n>.Nin_NX_NVN.szs`, plus its `MarioOffsetDataW<n>.bin`
  camera table, written into your mod folder's `atmosphere/contents/0100EA80032EA000/romfs/Model/`.
  - The vanilla archive of the same world is the template: its shader archive and point markers are reused. Its scenery
    is replaced by your painted map (one flat terrain mesh with a copy of a vanilla ground material showing the map as an
    RGBA8 texture, evenly lit), and every point and bend becomes a bone at its map position, with a copy of the vanilla
    level marker (W points) or path dot (F points). The point/route tables use the game's CSV format.
  - The model writer is **kdx-bfres**, built on BfresLibrary (the engine inside Switch-Toolbox). It needs the free
    **.NET runtime 8 or newer**: install the ".NET Runtime" from https://dotnet.microsoft.com/download/dotnet/8.0 . The
    dialog says whether it is ready.
  - A map's world number must match the world it replaces (point names pick the level files); maps that break the game's
    rules are refused.
- **Fixed: right-dragging the 2D map raised an error** (`translate_view` was missing since 0.4); this was the
  "crashes on the 2D tab" report. Every method call in the interface is now checked automatically.

### How the export was checked

- The engine first had to pass a **round-trip gate**: Worlds 1, 3 and 7 loaded and saved through it came back
  identical in every model, bone, vertex, index, material and texture.
- An exported Starter Island: **wmtool reports 0 errors, 0 warnings and 5/5 points reachable**; Koopatlas DX's rules
  agree; every point and bend is a bone exactly at its map position; BfresLibrary reloads the model; the new texture,
  decoded with Yaz0moto's independent bntxlib, matches the painted map pixel for pixel. (`tests/test_export.py`: 17 checks.)
- **Not yet tested on a console.** What to check first: the world loads, the map texture shows, Mario stands on the
  points and walks the routes, and the lighting looks even.

### Limits of this first export

- The terrain is flat (no hills yet) and the exported world has no animated scenery, special route models or effects.
- Every W point uses the level marker and every F point the path dot.
- Cameras default to no offset (the camera follows Mario exactly) unless set per point.

## What's new in 0.5

- **Crash fix for the 2D tab, and no more silent closing.** Errors inside the 2D map or the 3D view are now shown in a
  dialog and written to a log, and Koopatlas DX keeps running (before, PyQt5 closed the program). Native crashes, such
  as in a graphics driver, are written to `native-crash.log`. *Help > Open the log folder* opens
  `%LOCALAPPDATA%\KoopatlasDX`; please send `koopatlasdx.log` if anything goes wrong. The 2D terrain picture is now
  drawn with plain Python numbers (some PyQt5 builds reject numpy's), falls back to a blank background instead of
  failing, and 4× multisampling is off (it can crash some Windows drivers).
- **World Maker** (third tab, Ctrl+4): build a new world from tiles.
  - Tile palette on the left (drag across it for a multi-tile stamp), canvas in the middle, map settings, layers,
    selection and checks on the right.
  - Tools: brush (B), eraser (E, with size), fill (U), rectangle (Q), eyedropper (I), point (N), route (P),
    select / move (V). Wheel zooms, right-drag pans, Delete removes the selected point or route.
  - Layers (Ground, Paths, Decoration by default): add, remove, reorder, rename, hide, lock.
  - Points use the game's naming (W1M0 start, W101… levels, W1T0 tower, W1C0 castle, W1K1 Toad house, F… stops,
    K… bends). Each has the point-table columns and a camera offset. Routes get any of the 23 route types.
  - The design is checked with **the game's rules and play-through** as you work.
  - One tile is 30 world units by default, about half of Mario (estimated from the map's own markers until Mario's
    world-map model is measured; change it under *Map*).
  - **Starter tileset of original art** (64 tiles: ground, water and hazards, every path piece, cliffs and bridges,
    decorations), or load any PNG sheet of 32×32 tiles.
  - Save/open `.kdxmap` projects; export a picture. `samples/Starter Island.kdxmap` is a small finished example.
  - **Exporting a painted world to the game needs the model writer (roadmap 0.7)**: the button explains this. Designs
    made now will export unchanged once it exists.
- Edit > Undo/Redo follow whichever tab you are in.

## What's new in 0.4

Built on the reverse engineering of the game's world-map code (`scripts/worldmap/wmtool.py` and `docs/WORLDMAP.md`):
Koopatlas DX's rules module uses the same limits and logic, and its checks match `wmtool check` on all 12 vanilla
worlds (same errors, warnings and play-through results).

- **Drag points directly**, in the 2D map and the 3D view: press on a point and drag. Pressing on empty space still
  pans (2D) or orbits (3D). *Edit > Lock points* (Ctrl+L) stops accidental moves. **Arrow keys** nudge the selected point
  by 1 unit (Shift: 10), snapped to the terrain.
- **Checks from the game's rules**: file-size limits, 256 points/routes, 4 nodes / 16 paths / 2 appear animations per exit,
  128 F points, course slots, unknown flags, broken paths, plus a **play-through** (every level cleared both ways from
  the start point) listing points and routes that can never be reached.
- **The point panel** uses the real column meanings (nodes, paths and appear animations for the normal and the secret
  exit) with live counters such as *3/16*, shows the point's class and **level file** (e.g. `W1T0` → `Course/1-21.sarc`),
  and highlights on the map every segment each exit opens (green normal, pink secret).
- **Flags chooser**: every flag the game knows, in the game's own groups.
- **Pick…** buttons: click a point on the map to add it to an exit's nodes, or to add a path to it.
- **Routes**: *Edit > Connect two points* (key C) creates a route with two clicks; routes can be deleted; all 23 route
  types are in the list.
- **Camera**: the selected point's camera (point + offset) is a cyan marker in both views. Drag it to change the offset
  (2D: X/Z; 3D: Shift-drag changes the height). *View from this camera* shows the point from its camera in 3D.
  *View > Cameras for all points* (K) shows every camera at once.

## What's new in 0.3

- **FastYZ compression built in** (MIT, AboodXD): exported world archives are compressed in a fraction of a second, and
  world archives open about 30 times faster. It ships as a small native library (`kdx/fastyz/`), so no compiler or extra
  Python package is needed. If it cannot load, the built-in code is used (slower; exports are then uncompressed).
- The MarioOffset panel is now **Camera offset**: where the camera sits relative to Mario at that point, as found by
  testing in game. The fourth value is still unknown.
- The model reader accepts BFRES versions 5 to 8 (the game also contains some 5.0.2 files).
- Path edits made with Koopatlas DX have been **tested in game and work**.

## What's new in 0.2

- **2D map** (the first tab): the world drawn from straight above, with every point, waypoint and route on top.
  - Click a point to select it (the nearest marker wins when markers overlap); double-click to centre on it.
  - *Edit > Move points* (key M), then drag points or waypoints. With *Snap to the terrain* on, the height is taken
    from the ground under the new spot. Routes follow while you drag.
  - Wheel to zoom, right- or middle-drag to pan, *View > 2D grid* (G), labels (L), waypoints (W), routes (R).
  - The 2D map and the 3D view share the same selection, edits and undo; switch with Ctrl+2 / Ctrl+3.
  - Unticking a model also removes it from the 2D picture.
- **ROMFS setup and the Worlds panel**: offset tables and the game text are taken from the romfs folder automatically.
  *File > Set ROMFS folder…* changes it (and the text language).

## Everything you can edit

- Point positions (2D drag, 3D drag, or typed in the Properties panel). Only that bone's position changes.
- The point table: flags and both unlock pairs (points unlocked, routes revealed).
- Route types (path, slope, jump, cloud, stone…).
- MarioOffset records (per point, or the whole table in *Tools > Offset tables…*) and the raw LookOffset values.
- Undo/redo for every edit; a **Checks** panel (bones, IDs, MarioOffset table in step, 128-point limit per archive).
- **Export** (*File > Export to mod folder…*, Ctrl+E): changed files go to
  `atmosphere/contents/0100EA80032EA000/romfs/Model/`, are read back and checked.
- Names come from the game's text (`romfs/Message/CommonMsg_<language>.szs`); edit the text itself with your MSBT
  editor (*Tools > Open the game text in your MSBT editor*; folder or .exe set in Preferences).

## What has and has not been verified

**Verified** by automated tests on the game's real files (70 core + 89 interface + 17 export checks), including: all 18 offset tables
write back byte-identical; a moved point changes only its bone's 12 bytes; exports reopen with the edits; on the 2D map,
clicking selects the right point when markers overlap, dragging moves the point onto the terrain, routes follow, undo puts
it back and the 3D view agrees; the ROMFS dialog accepts romfs, its parent or Model and refuses other folders; the Worlds
panel lists the 12 worlds with their names and opens them with their offset tables.

**Not verified yet — please test on a backup:**
- Path edits have been tested in game; moved points, MarioOffset and LookOffset edits not yet. The FastYZ Windows DLL
  is cross-compiled and checked but has not been run on Windows yet (if it fails to load, exports are saved uncompressed).
- What the MarioOffset vector and flags do; that the game reads point positions only from the bones; most flag meanings.
- The 2D map edits existing worlds. **Painting brand-new 2D worlds from tiles** (the plan's Method B) needs the model
  writer that comes with adding new geometry, so it is not in 0.2.
- Textures and the game's shaders are not shown yet (flat colours). The 3D view needs OpenGL 3.3.

## Game shaders (View > Shading)

**View > Shading > Game shaders** draws the 3D view with the world's own shader programs instead of the simple
one-light renderer. Every material's Maxwell (NVN) vertex and pixel program is taken from the shader archive inside the
world archive (`Wii_UBER.Nin_NX_NVN.bfsha` next to `output.bfres`), translated to GLSL 4.50 and bound the way NVN binds
it, so the light maps, bake maps, normal and specular maps, route edges and water look the way the game draws them.

- **Which program:** the shading model the material names (`nw4f_cs_shader`, `nw4f_cs_route_shader`,
  `nw4f_cs_water_shader`, ...) and, inside it, the program whose option key matches the material's shader options. The
  `gsys_renderstate` and alpha-test options come from the material's render state, as the engine sets them (leaves are
  "mask" with the alpha test on even where their stored options say otherwise).
- **Binding:** NVN uniform slot *s* is Maxwell constant buffer *s* + 3 (ShaderOption, Material, MdlEnvView, MdlMtx,
  Shp); samplers are found through the program's reflection (`cTexMap0`, `cBakeMap0`, `cLightMap0`, ...) and the
  shading model's symbol table. Shapes on a bone get the bone's world matrix in MdlMtx, so skeletal animations move them;
  texture-matrix animations feed the material block.
- **Light maps** (`lmDifCSW1a`, `lmSpcCSW1a`, ...): the copies in the BFRES are grey placeholders; the game makes the
  real ones at run time, so they are baked from `Env/env_pack.szs` (`CS_All.bagllmap` and the `CS_W*.baglenv` lights)
  for the camera's current angle. This needs the romfs folder chosen in the ROMFS dialog; without it the placeholders
  are used and the world looks darker.
- **Things the game renders at run time that the editor does not:** the route mask (the reveal ramp that a route's
  animation slides along it) reads as fully open, so every route shows; the shadow map reads as fully lit; the water's
  reflection target reflects the water's own colour.
- **Translator** (View > Shading > Translator): *Built-in* (the default, pure Python, below) or *Ryujinx.ShaderTools*,
  the emulator's own command-line translator. Koopatlas DX does not include or download it: point *Set
  Ryujinx.ShaderTools path…* at the `Ryujinx.ShaderTools.exe` from your own Ryujinx / Ryubing build (or set
  `KDX_SHADERTOOLS`). Both give the same picture (W1 and W3 differ by at most 1/255 per pixel). Translations are cached
  in `%LOCALAPPDATA%\KoopatlasDX\cache\shaders`.
- **Needs OpenGL 4.5.** The 3D view asks for a 4.5 context when the driver offers one. Without it, without a shader
  archive, or for a material whose program does not compile, the simple renderer is used (View > Shading > Shader status
  says why). Every material of W1 (68) and W3 (86) is drawn with its own program.

The translator is Yaz0loom-DX's (`kdx/shader/`: `decoder.py`, `translator.py`, `maxwell_tables.py`, `bfsha.py`,
`aamp.py`, plus the light-map baker in `lightmaps.py`), built on Ryujinx / Ryubing (MIT, see
`kdx/shader/LICENSE-Ryujinx.txt`): `maxwell_tables.py` is generated from Ryujinx's `InstTable.cs` / `InstDecoders.cs`,
and the instruction semantics follow Ryujinx's emitters. The output uses the same GLSL interface as
Ryujinx.ShaderTools, which is why both translators bind the same way.

## Tests

    python tests/test_models.py [<romfs folder> [<NSMBW folder>]]   (FBX/DAE/OBJ/Wii models, editors, NSMBW port)
    python tests/test_core.py <folder with your CS_*.szs, *OffsetData*.bin, CommonMsg_USen.szs / .msbt files>
    python tests/test_gui.py  <same folder>          (Linux without a display: xvfb-run -a python3 tests/test_gui.py …)

The ROMFS checks in `test_gui.py` look for a dump laid out as `<KDX_ROMFS_DUMP>/romfs/Model` (default
`/tmp/romfs_test/dump`) and are skipped if it is missing. Game files are not included. BFRES layout reference:
BfresLibrary (MIT). Game shaders: Yaz0loom-DX's Maxwell translator, built on Ryujinx / Ryubing (MIT). Named after
Treeki's Koopatlas for NSMBW.

    python tests/test_render.py <romfs folder>      (3D view with real OpenGL: textures, animations, ride along, game shaders)

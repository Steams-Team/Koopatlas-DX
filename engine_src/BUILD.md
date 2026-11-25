# Building the kdx-bfres engine

The engine in `kdx/engine/` is a portable .NET 8 build (runs on .NET 8 or newer, Windows, Linux or macOS). To rebuild it:

1. Install the .NET 8 SDK.
2. In this folder: `git clone https://github.com/KillzXGaming/BfresLibrary`
3. BfresLibrary predates `System.Half`; on .NET 8 its `ReadHalf` extension is hidden by `BinaryReader.ReadHalf`. In
   `BfresLibrary/BfresLibrary/Shared/Core/Extensions/BinaryDataReaderExtensions.cs` rename the extension
   `ReadHalf(this BinaryDataReader self)` to `ReadHalfBfres(...)`, and replace every `self.ReadHalf()` in the
   BfresLibrary sources with `self.ReadHalfBfres()`.
4. `dotnet build -c Release`, then copy `bin/Release/net8.0/` (kdx-bfres.dll, .runtimeconfig.json, .deps.json and the
   Syroot/Newtonsoft DLLs) into `kdx/engine/`.

BfresLibrary is MIT-licensed (see `kdx/engine/BfresLibrary-LICENSE.txt`).

## Since 0.8

- `BfresLibrary/` is kept here (from `Downloads/Reverse/third_party/BfresLibrary`), with the `ReadHalf` rename of
  step 3 already applied, so `dotnet build -c Release` works offline. Copy **all** of `bin/Release/net8.0/*.dll` and
  the `.deps.json`/`.runtimeconfig.json` into `kdx/engine/`: this BfresLibrary uses Syroot.BinaryData 2.0.2.
- Gate before installing a rebuild: `kdx-bfres roundtrip` of vanilla worlds must give the same bytes as the
  installed writer (checked for W1 and W7 when this copy was installed).
- Commands: `info`, `roundtrip`, `build` (terrain as one part or a list of `parts`, one material copy each;
  `index_format` "u32" for parts over 65,535 vertices) and `anims` (every material and skeletal animation as JSON,
  curves raw; evaluated by `kdx/anim.py`).

## Since 0.8.1

- New command `materials <in.bfres> <out.json>`: per model and material, the shader archive and shading model, shader
  options, shader parameters (values), sampler assignments, texture names and sampler wrap modes. Installed after the
  round-trip gate (W1 and W7 byte-identical to the previous writer).
- The .NET 9/10 SDKs build this net8.0 project as is (`C:\Program Files\dotnet\dotnet.exe build -c Release`).
- `build`: textures may be given as `{"bc1": <blocks file>}` (BC1, swizzled per 4x4 block) or
  `{"neutral_light": [brightness, alpha]}` (a baked-light texture that is grey once scaled by the copied material's
  `gsys_bake_light_scale`); terrain texture maps take `bake0`/`bake1` (Bake_*_b00 / _b01), and lightmaps not named in
  the map keep the template's. Gate passed (W1, W7 byte-identical) after each change.

## Since 0.9

- `build`: each terrain part may say `"mode": "opaque" | "mask" | "translucent"`. Its material is a copy of the
  template's largest material drawn that way (`gsys_render_state_mode`) whose shape reads no vertex attribute beyond
  `_p0 _n0 _c0 _u0 _u1` and whose textures are only `_alb`/`_spm`/`lmDif`/`lmSpc`/`Bake_*`. World 1: mask ->
  `mtKinokoRed`; no translucent material qualifies, so translucent parts use the mask donor. The donors are printed
  (`donors: opaque=..., mask=..., translucent=...`). Gate passed (W1, W7 byte-identical to the 0.8.1 writer).

## Since 0.9.4
- `build`: a part with `"route": true` copies the template's path material: `nw4f_cs_route_shader`, `MaskOpaque` and
  `_msk` preferred, so World 1's secret `mtRW102W1S0a` is skipped. Its vertices carry 24 floats: the usual 14, then
  tangent xyzw, binormal xyzw and mask uv (`_u2`). `"mask_offset"` becomes its `texmtx0` translate v (where
  `MaskRouteAnimation` starts). The texture keys `edge_mask` (`_msk`) and `normal` (`_nml`) replace those refs.

## Since 0.9.3
- `materials` also lists each material's `attrib_assigns` (shader attribute -> vertex attribute). Vanilla World 1's
  `mtGrass` reads `_c0`, but that `_c0` averages 0.11 (it is a mask), which is why ported Wii vertex shading is baked
  into textures (`kdx.model3d.shade_groups`) and not into `_c0`.

## Since 0.9.2

- `edit` takes `shape_edits [{model, shape, matrices, per_slot, texture, replace}]`:
  - `matrices` (row-major 4x4s in the space the vertices are stored in: the shape's bone for skin count 0, one per
    skin slot with `per_slot` for rigid skinning, the model for smooth skinning) move positions, normals, tangents and
    bitangents. A mirroring matrix reverses the triangles; the culling bounds are remade.
  - `texture`: the shape gets its own copy of its material, with `replace` (its albedo) swapped for that texture
    (added through `textures`).
- `textures` are added whether or not there is an `add`.

## Since 0.9.1

- `build` parts take `"wrap": [u, v]` (repeat / clamp / mirror), set on the copied material's albedo sampler
  (`TexSampler.ClampX/Y`: Wrap, ClampBorder = Switch ClampToEdge, Mirror).
- New command `edit <spec.json>`: change a game world's models and keep everything else. `input`, `output`,
  `remove_shapes [{model, shape}]`, `replace_textures [{name, width, height, rgba | bc1}]`, `textures [...]` and
  `add {model, parts [...]}` (the same part format as `build`, appended with copies of that model's own donors).
- `build` and `edit` share `Donors`, `AddTextures` and `AddParts`. Gate passed (W1, W7 byte-identical).

## Since 0.9.11
- 0.14.0: new command `synchro <spec.json>` moves unskinned shapes into the decoration model `CS_W<n>_Synchro`, which every world builds and loops (F0335).
  - Each item gets bones `kdx_sNN_pivot` / `kdx_sNN` / `kdx_sNN_in` and a looping skeletal animation of the same name.
  - Channels `sx..tz`, rotation in degrees; modes linear, smooth, step and hermite (slopes in value per frame).
  - Only the materials its shapes use are kept.
  - FlagsScale comes from the input, else from `flags_from`.
  - Also `fmaadump <bfres> <json>`: a reflection dump of every material animation (layout reference).
  - Installed after the W1/W7 roundtrip gate (identical bytes).
- New command `marker <spec.json>`: rebuilds only the map-point model's skeleton in the given order (flat under the root), keeping each existing bone's marker shapes and giving new bones a copy of a vanilla marker. Used when points or paths are added in the main window. Installed after `roundtrip` of vanilla W1 and W7 gave the same bytes as the previous build.
- New command `routecopy <spec.json>`: copies a path appear animation (model, skeletal anim, _fts/_fcl/_ftp/_fsp/_fvs material anims) under another path name, root bone renamed and moved. The Switch loader files material anims by name (an `_fts` lands in ShaderParamAnims), so all five dictionaries are searched. Installed after the W1/W7 roundtrip gate.
- 0.10.2: `routemake` keys also take `rotate` ([frame, x, y, z] in degrees, Euler XYZ) and `interp` ({scale|rotate|translate: "linear"|"smooth"}); every curve is now Cubic (linear segment: [v0, d, 0, 0]; smooth: [v0, 0, 3d, -2d]), in the vanilla order scale, rotate, translate, base pose = first key. Installed after the W1/W7 roundtrip gate (identical bytes).
- New command `routemake <spec.json>`: moves unskinned terrain shapes into a new path model (bones r<path>, kdx_pivot, kdx_appear, kdx_inner) and writes a linear skeletal animation of the same name built from scale/translate keys. BfresLibrary's SkeletalAnimConvert.FromStruct overwrites FlagsCurve per curve, so the curves are built directly. Installed after the W1/W7 roundtrip gate.

// kdx-bfres - the Koopatlas DX model writer, built on BfresLibrary (MIT, KillzXGaming/Syroot).
//
//   kdx-bfres roundtrip <in.bfres> <out.bfres>     load and save unchanged (the safety gate)
//   kdx-bfres info <in.bfres>                      models, bones, skinning, textures (JSON on stdout)
//   kdx-bfres build <spec.json>                    a new world model from a vanilla template (see BuildWorld)
//
// "build" never invents structures the game has not seen: the terrain material, the point markers and the texture
// settings are all copied from the template world, and only names, positions, geometry and pixels change.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BfresLibrary;
using BfresLibrary.GX2;
using BfresLibrary.Helpers;
using Newtonsoft.Json.Linq;
using Syroot.BinaryData;
using Syroot.Maths;
using Bntx = Syroot.NintenTools.NSW.Bntx;

static class Program
{
    static int Main(string[] args)
    {
        try
        {
            if (args.Length >= 3 && args[0] == "roundtrip") { var r = new ResFile(args[1]); r.Save(args[2]); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 2 && args[0] == "info") { Console.WriteLine(Info(new ResFile(args[1])).ToString()); return 0; }
            if (args.Length >= 3 && args[0] == "anims") { File.WriteAllText(args[2], Anims(new ResFile(args[1])).ToString()); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 3 && args[0] == "materials") { File.WriteAllText(args[2], Materials(new ResFile(args[1])).ToString()); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 2 && args[0] == "build") { BuildWorld(JObject.Parse(File.ReadAllText(args[1]))); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 4 && args[0] == "rename") { Rename(args[1], args[2], args[3], args.Length > 4 ? args[4] : args[1]); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 2 && args[0] == "routemake") { RouteMake(JObject.Parse(File.ReadAllText(args[1]))); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 2 && args[0] == "routecopy") { RouteCopy(JObject.Parse(File.ReadAllText(args[1]))); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 2 && args[0] == "marker") { RebuildMarker(JObject.Parse(File.ReadAllText(args[1]))); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 3 && args[0] == "fmaadump") { File.WriteAllText(args[2], FmaaDump(new ResFile(args[1])).ToString()); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 2 && args[0] == "synchro") { Synchro(JObject.Parse(File.ReadAllText(args[1]))); Console.WriteLine("ok"); return 0; }
            if (args.Length >= 2 && args[0] == "edit") { EditWorld(JObject.Parse(File.ReadAllText(args[1]))); Console.WriteLine("ok"); return 0; }
            Console.Error.WriteLine("usage: kdx-bfres roundtrip <in> <out> | info <in> | build <spec.json> | edit <spec.json>");
            return 2;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("ERROR: " + e.GetType().Name + ": " + e.Message);
            Console.Error.WriteLine(e.StackTrace);
            return 1;
        }
    }

    // ---- info ------------------------------------------------------------------------------------------------------
    static JObject Info(ResFile res)
    {
        var o = new JObject { ["version"] = $"{res.VersionMajor}.{res.VersionMinor}.{res.VersionMinor2}" };
        var models = new JArray();
        foreach (var kv in res.Models)
        {
            var m = kv.Value;
            var bones = new JArray();
            foreach (var b in m.Skeleton.Bones.Values)
                bones.Add(new JObject { ["name"] = b.Name, ["parent"] = b.ParentIndex, ["smooth"] = b.SmoothMatrixIndex, ["rigid"] = b.RigidMatrixIndex });
            models.Add(new JObject
            {
                ["name"] = kv.Key, ["bones"] = bones, ["shapes"] = m.Shapes.Count, ["materials"] = new JArray(m.Materials.Keys),
                ["matrix_to_bone"] = m.Skeleton.MatrixToBoneList?.Count ?? 0,
                ["inverse_matrices"] = m.Skeleton.InverseModelMatrices?.Count ?? 0,
            });
        }
        o["models"] = models;
        o["external"] = new JArray(res.ExternalFiles.Keys);
        o["animations"] = res.SkeletalAnims.Count + res.MaterialAnims.Count + res.BoneVisibilityAnims.Count + res.ShapeAnims.Count + res.SceneAnims.Count;
        return o;
    }

    // ---- helpers -----------------------------------------------------------------------------------------------------
    // ---- anims: every material and skeletal animation as JSON, curves raw (Python evaluates them: kdx/anim.py) ------
    static JObject Curve(AnimCurve c)
    {
        int n = c.Keys.GetLength(0), m = c.Keys.GetLength(1);
        var keys = new JArray();
        for (int i = 0; i < n; i++) { var k = new JArray(); for (int j = 0; j < m; j++) k.Add(c.Keys[i, j]); keys.Add(k); }
        return new JObject {
            ["target"] = c.AnimDataOffset, ["type"] = c.CurveType.ToString(), ["frames"] = new JArray(c.Frames.Select(f => (double)f)),
            ["keys"] = keys, ["scale"] = c.Scale, ["offset"] = c.Offset.Single, ["offset_int"] = c.Offset.Int32,
            ["start"] = c.StartFrame, ["end"] = c.EndFrame, ["delta"] = c.Delta,
        };
    }

    static JObject MatAnim(MaterialAnim a, string kind)
    {
        var mats = new JArray();
        foreach (var d in a.MaterialAnimDataList)
        {
            var parms = new JArray();
            foreach (var p in d.ParamAnimInfos ?? new List<ParamAnimInfo>())
            {
                var curves = new JArray();
                for (int i = p.BeginCurve; i < p.BeginCurve + p.FloatCurveCount + p.IntCurveCount && i < d.Curves.Count; i++)
                    curves.Add(Curve(d.Curves[i]));
                var consts = new JArray();
                for (int i = p.BeginConstant; i < p.BeginConstant + p.ConstantCount && d.Constants != null && i < d.Constants.Count; i++)
                    consts.Add(new JObject { ["target"] = d.Constants[i].AnimDataOffset, ["value"] = d.Constants[i].Value.Single,
                                             ["int"] = d.Constants[i].Value.Int32 });
                parms.Add(new JObject { ["param"] = p.Name, ["curves"] = curves, ["constants"] = consts });
            }
            var pats = new JArray();
            foreach (var p in d.PatternAnimInfos ?? new List<PatternAnimInfo>())
            {
                var o = new JObject { ["sampler"] = p.Name, ["curve_index"] = p.CurveIndex, ["begin_constant"] = p.BeginConstant };
                if (p.CurveIndex >= 0 && p.CurveIndex < d.Curves.Count) o["curve"] = Curve(d.Curves[p.CurveIndex]);
                pats.Add(o);
            }
            var bases = new JArray((d.BaseDataList ?? new ushort[0]).Select(x => (int)x));
            mats.Add(new JObject { ["material"] = d.Name, ["params"] = parms, ["patterns"] = pats, ["base"] = bases });
        }
        return new JObject { ["name"] = a.Name, ["kind"] = kind, ["frames"] = a.FrameCount, ["loop"] = a.Loop,
                             ["textures"] = new JArray((a.TextureNames?.Keys ?? new List<string>()).ToArray()), ["materials"] = mats };
    }

    static JObject Anims(ResFile res)
    {
        var mat = new JArray();
        foreach (var (kind, dict) in new[] { ("fsp", res.ShaderParamAnims), ("fts", res.TexSrtAnims), ("fcl", res.ColorAnims),
                                             ("ftp", res.TexPatternAnims), ("fvs", res.MatVisibilityAnims) })
            foreach (var a in dict.Values) mat.Add(MatAnim(a, kind));
        var skel = new JArray();
        foreach (var a in res.SkeletalAnims.Values)
        {
            var bones = new JArray();
            foreach (var b in a.BoneAnims)
            {
                var bd = b.BaseData;
                bones.Add(new JObject {
                    ["bone"] = b.Name, ["flags_base"] = (int)b.FlagsBase, ["flags_curve"] = (int)b.FlagsCurve,
                    ["base_scale"] = new JArray(bd.Scale.X, bd.Scale.Y, bd.Scale.Z),
                    ["base_rotate"] = new JArray(bd.Rotate.X, bd.Rotate.Y, bd.Rotate.Z, bd.Rotate.W),
                    ["base_translate"] = new JArray(bd.Translate.X, bd.Translate.Y, bd.Translate.Z),
                    ["curves"] = new JArray(b.Curves.Select(Curve)),
                });
            }
            skel.Add(new JObject { ["name"] = a.Name, ["frames"] = a.FrameCount, ["loop"] = a.Loop,
                                   ["rotate"] = a.FlagsRotate.ToString(), ["bones"] = bones });
        }
        return new JObject { ["material_anims"] = mat, ["skeletal_anims"] = skel };
    }

    // ---- materials: shader assignment (archive, shading model, options) and shader parameters per material ------------
    static JToken ParamValue(object v)
    {
        switch (v)
        {
            case null: return JValue.CreateNull();
            case float f: return f;
            case int i: return i;
            case uint u: return u;
            case bool b: return b;
            case float[] fa: return new JArray(fa.Select(x => (double)x));
            case int[] ia: return new JArray(ia);
            case uint[] ua: return new JArray(ua);
            case bool[] ba: return new JArray(ba);
            case Srt2D s: return new JObject { ["scale"] = new JArray(s.Scaling.X, s.Scaling.Y), ["rotate"] = s.Rotation, ["translate"] = new JArray(s.Translation.X, s.Translation.Y) };
            case TexSrt t: return new JObject { ["mode"] = t.Mode.ToString(), ["scale"] = new JArray(t.Scaling.X, t.Scaling.Y), ["rotate"] = t.Rotation, ["translate"] = new JArray(t.Translation.X, t.Translation.Y) };
            default: return v.ToString();
        }
    }

    static JObject Materials(ResFile res)
    {
        var models = new JObject();
        foreach (var kv in res.Models)
        {
            var mats = new JObject();
            foreach (var mk in kv.Value.Materials)
            {
                var m = mk.Value;
                var sa = m.ShaderAssign;
                var opts = new JObject();
                if (sa?.ShaderOptions != null) foreach (var o in sa.ShaderOptions) opts[o.Key] = o.Value?.ToString();
                var samplers = new JObject();
                if (sa?.SamplerAssigns != null) foreach (var o in sa.SamplerAssigns) samplers[o.Key] = o.Value?.ToString();
                var attribs = new JObject();                 // shader attribute -> vertex attribute (_c0 read or not)
                if (sa?.AttribAssigns != null) foreach (var o in sa.AttribAssigns) attribs[o.Key] = o.Value?.ToString();
                var parms = new JObject();
                foreach (var p in m.ShaderParams) parms[p.Key] = new JObject { ["type"] = p.Value.Type.ToString(), ["value"] = ParamValue(p.Value.DataValue) };
                mats[mk.Key] = new JObject
                {
                    ["archive"] = sa?.ShaderArchiveName, ["shading_model"] = sa?.ShadingModelName, ["options"] = opts,
                    ["sampler_assigns"] = samplers, ["attrib_assigns"] = attribs, ["params"] = parms,
                    ["textures"] = new JArray(m.TextureRefs.Select(t => t.Name)), ["samplers"] = new JArray(m.Samplers.Keys),
                    ["wrap"] = new JObject(m.Samplers.Keys.Select(k => (object)new JProperty(k,
                        m.Samplers[k].TexSampler.ClampX + "/" + m.Samplers[k].TexSampler.ClampY)).ToArray()),
                };
            }
            models[kv.Key] = mats;
        }
        return new JObject { ["models"] = models };
    }

    static string Tmp() => Path.Combine(Path.GetTempPath(), "kdx_" + Guid.NewGuid().ToString("N"));

    // Deep copies built from fields (BfresLibrary's section export/import does not read back Switch shapes).
    static VertexBuffer CopyVertexBuffer(VertexBuffer src)
    {
        var h = new VertexBufferHelper(src, ByteOrder.LittleEndian);
        var n = new VertexBufferHelper { ByteOrder = ByteOrder.LittleEndian, Attributes = new List<VertexBufferHelperAttrib>() };
        foreach (var a in h.Attributes)
            n.Attributes.Add(new VertexBufferHelperAttrib { Name = a.Name, Format = a.Format, Data = a.Data.ToArray() });
        return n.ToVertexBuffer();
    }

    static Shape CopyShape(Shape s)
    {
        var c = new Shape
        {
            Name = s.Name, Flags = s.Flags, MaterialIndex = s.MaterialIndex, BoneIndex = s.BoneIndex,
            VertexBufferIndex = s.VertexBufferIndex, VertexSkinCount = s.VertexSkinCount, TargetAttribCount = s.TargetAttribCount,
            RadiusArray = new List<float>(s.RadiusArray ?? new List<float>()),
            BoundingRadiusList = new List<Vector4F>(s.BoundingRadiusList ?? new List<Vector4F>()),
            SkinBoneIndices = new List<ushort>(s.SkinBoneIndices ?? new List<ushort>()),
            KeyShapes = new ResDict<KeyShape>(),
            SubMeshBoundings = (s.SubMeshBoundings ?? new List<Bounding>()).Select(b => new Bounding { Center = b.Center, Extent = b.Extent }).ToList(),
            SubMeshBoundingNodes = new List<BoundingNode>(s.SubMeshBoundingNodes ?? new List<BoundingNode>()),
            SubMeshBoundingIndices = new List<ushort>(s.SubMeshBoundingIndices ?? new List<ushort>()),
        };
        c.Meshes = new List<Mesh>();
        foreach (var m in s.Meshes)
        {
            var nm = new Mesh { PrimitiveType = m.PrimitiveType, FirstVertex = m.FirstVertex };
            nm.SetIndices(m.GetIndices().ToList(), m.IndexFormat);
            nm.SubMeshes = m.SubMeshes.Select(x => new SubMesh { Offset = x.Offset, Count = x.Count }).ToList();
            c.Meshes.Add(nm);
        }
        c.VertexBuffer = CopyVertexBuffer(s.VertexBuffer);
        return c;
    }

    static float[] ReadFloats(string path)
    {
        var b = File.ReadAllBytes(path);
        var f = new float[b.Length / 4];
        System.Buffer.BlockCopy(b, 0, f, 0, f.Length * 4);
        return f;
    }

    // ---- Tegra X1 block-linear swizzle (RGBA8: 4 bytes per pixel, 1x1 blocks) ---------------------------------------
    static uint DivRoundUp(uint n, uint d) => (n + d - 1) / d;
    static uint RoundUp(uint x, uint y) => ((x - 1) | (y - 1)) + 1;
    static uint Pow2RoundUp(uint x) { x--; x |= x >> 1; x |= x >> 2; x |= x >> 4; x |= x >> 8; x |= x >> 16; return x + 1; }

    public static uint BlockHeight(uint height)
    {
        uint bh = Pow2RoundUp(height / 8);
        if (bh > 16) bh = 16;
        if (bh < 1) bh = 1;
        return bh;
    }

    static uint AddrBlockLinear(uint x, uint y, uint width, uint bpp, uint blockHeight)
    {
        uint widthInGobs = DivRoundUp(width * bpp, 64);
        uint gob = (y / (8 * blockHeight)) * 512 * blockHeight * widthInGobs
                 + (x * bpp / 64) * 512 * blockHeight
                 + (y % (8 * blockHeight) / 8) * 512;
        x *= bpp;
        return gob + ((x % 64) / 32) * 256 + ((y % 8) / 2) * 64 + ((x % 32) / 16) * 32 + (y % 2) * 16 + (x % 16);
    }

    public static byte[] Swizzle(byte[] linear, uint width, uint height, uint bpp, uint blockHeight)
    {
        uint pitch = RoundUp(width * bpp, 64);
        uint size = pitch * RoundUp(height, blockHeight * 8);
        var outb = new byte[size];
        for (uint y = 0; y < height; y++)
            for (uint x = 0; x < width; x++)
            {
                uint dst = AddrBlockLinear(x, y, width, bpp, blockHeight);
                uint src = (y * width + x) * bpp;
                System.Buffer.BlockCopy(linear, (int)src, outb, (int)dst, (int)bpp);
            }
        return outb;
    }

    // rgba: RGBA8 pixels, or with bc1 the BC1 blocks (8 bytes per 4x4 block, rows of blocks), swizzled per block
    // mips > 1 (BC1 only): rgba holds every level's blocks one after the other (level 0 first). Each level is swizzled
    // with its own block height (halved while the level is no taller than half of it, as the game's own textures:
    // Bake_CS_W1_World 1024x512 BC3 has 11 levels at 0, 524288, 655360...) and the levels are packed back to back.
    // Bntx keeps TextureData[0][level] as the bytes from that level's offset to the end (how it loads vanilla ones).
    static Bntx.Texture NewTexture(Bntx.Texture like, string name, uint w, uint h, byte[] rgba, bool bc1 = false, int mips = 1)
    {
        uint bh = bc1 ? BlockHeight(DivRoundUp(h, 4)) : BlockHeight(h);
        byte[] data;
        var offsets = new List<long> { 0 };
        if (bc1 && mips > 1)
        {
            var all = new List<byte>();
            int src = 0;
            offsets.Clear();
            for (int l = 0; l < mips; l++)
            {
                uint lw = Math.Max(1u, w >> l), lh = Math.Max(1u, h >> l);
                uint wb = DivRoundUp(lw, 4), hb = DivRoundUp(lh, 4);
                uint lbh = bh;
                while (lbh > 1 && hb <= (lbh / 2) * 8) lbh /= 2;
                int n = (int)(wb * hb * 8);
                var lin = new byte[n];
                System.Buffer.BlockCopy(rgba, src, lin, 0, n);
                src += n;
                offsets.Add(all.Count);
                all.AddRange(Swizzle(lin, wb, hb, 8, lbh));
            }
            data = all.ToArray();
        }
        else
        {
            mips = 1;
            data = bc1 ? Swizzle(rgba, DivRoundUp(w, 4), DivRoundUp(h, 4), 8, bh) : Swizzle(rgba, w, h, 4, bh);
        }
        var levels = offsets.Select(o => data.Skip((int)o).ToArray()).ToList();
        var t = new Bntx.Texture
        {
            Name = name, Path = "", Width = w, Height = h, Depth = 1, MipCount = (uint)mips, ArrayLength = 1,
            Format = bc1 ? (Bntx.GFX.SurfaceFormat)0x1A01 : Bntx.GFX.SurfaceFormat.R8_G8_B8_A8_UNORM,   // BC1_UNORM
            ChannelRed = Bntx.GFX.ChannelType.Red, ChannelGreen = Bntx.GFX.ChannelType.Green,
            ChannelBlue = Bntx.GFX.ChannelType.Blue, ChannelAlpha = Bntx.GFX.ChannelType.Alpha,
            Dim = like.Dim, SurfaceDim = like.SurfaceDim, TileMode = Bntx.GFX.TileMode.Default, Swizzle = 0,
            AccessFlags = like.AccessFlags, Flags = like.Flags, SampleCount = 1,
            textureLayout = 0, textureLayout2 = like.textureLayout2,
            Regs = new uint[like.Regs?.Length ?? 13], MipOffsets = offsets.ToArray(), Alignment = 512,
            ReadTextureLayout = 1, BlockHeightLog2 = (uint)Math.Log(bh, 2), ImageSize = (uint)data.Length,
            TextureData = new List<List<byte[]>> { levels },
            UserData = new List<Bntx.UserData>(), UserDataDict = new Bntx.ResDict(),
        };
        return t;
    }

    static readonly string[] Needed = { "_p0", "_n0", "_c0", "_u0", "_u1" };

    // The ground material every part copies: the model's largest shape in the vertex layout we write (donor), and
    // donors per render mode (gsys_render_state_mode): a part with "mode": "mask" (alpha cut-out) or "translucent"
    // copies the largest material drawn that way, among those whose shape reads no vertex attribute beyond the layout
    // we write (an extra one such as _c0 is simply unread) and whose textures are only albedo/specular/lightmaps/bakes
    // (no normal map or mask the part could not fill).
    // World 1: mask -> mtKinokoRed (_p0 _n0 _u0 _u1), translucent -> none simple enough, so it falls back to mask.
    // Route donor: a vanilla path material (nw4f_cs_route_shader: mtRW101W102a...) and its shape's layout
    // (_p0 _n0 _t0 _b0 _u0 albedo, _u1 bake, _u2 reveal mask). MapModel::createRouteAnimation_ gives every material
    // mt<route><a-h> of a route named in the point table a MaskRouteAnimation, which hides it at its own texmtx0
    // translate and raises that to -0.8 when the route opens (sweeping MaskOpaque along _u2).
    static readonly string[] RouteNeeded = { "_p0", "_n0", "_t0", "_b0", "_u0", "_u1", "_u2" };

    static Shape RouteDonor(Model terrain, out Material mat)
    {
        var s = terrain.Shapes.Values
            .Where(x => terrain.Materials[x.MaterialIndex].ShaderAssign?.ShadingModelName == "nw4f_cs_route_shader"
                        && terrain.Materials[x.MaterialIndex].ShaderParams.ContainsKey("texmtx0")
                        && x.VertexBuffer.Attributes.Count == RouteNeeded.Length
                        && RouteNeeded.All(a => x.VertexBuffer.Attributes.ContainsKey(a)))
            // a plain path (MaskOpaque, the mask NSMBW's texMaskOpaque matches, plus the _msk edge mask) before the
            // secret ones (World 1's mtRW102W1S0a has its own "Mask" and no edge mask)
            .OrderByDescending(x => terrain.Materials[x.MaterialIndex].TextureRefs.Any(t => t.Name == "MaskOpaque")
                                    && terrain.Materials[x.MaterialIndex].TextureRefs.Any(t => t.Name.EndsWith("_msk")))
            .ThenByDescending(x => x.Meshes[0].IndexCount).FirstOrDefault();
        mat = s == null ? null : terrain.Materials[s.MaterialIndex];
        if (s != null) Console.WriteLine("route donor: " + mat.Name);
        return s;
    }

    static Dictionary<string, Material> Donors(Model terrain, string name, out Shape donor)
    {
        donor = terrain.Shapes.Values
            .Where(s => s.VertexBuffer.Attributes.Count == Needed.Length && Needed.All(a => s.VertexBuffer.Attributes.ContainsKey(a)))
            .OrderByDescending(s => s.Meshes[0].IndexCount).FirstOrDefault()
            ?? throw new Exception("no ground shape with the expected vertex layout in " + name);
        Material donorMat = terrain.Materials[donor.MaterialIndex];
        var modeDonor = new Dictionary<string, Material> { ["opaque"] = donorMat };
        foreach (var s in terrain.Shapes.Values
                     .Where(s => s.VertexBuffer.Attributes.Keys.All(k => Needed.Contains(k)) && s.VertexBuffer.Attributes.ContainsKey("_p0"))
                     .OrderByDescending(s => s.Meshes[0].IndexCount))
        {
            var m = terrain.Materials[s.MaterialIndex];
            string mode = m.RenderInfos.ContainsKey("gsys_render_state_mode") ? m.RenderInfos["gsys_render_state_mode"].GetValueStrings().FirstOrDefault() ?? "opaque" : "opaque";
            bool simple = m.TextureRefs.All(t => t.Name.EndsWith("_alb") || t.Name.EndsWith("_spm") || t.Name.StartsWith("lmDif")
                                                 || t.Name.StartsWith("lmSpc") || t.Name.StartsWith("Bake_"));
            if (simple && !modeDonor.ContainsKey(mode)) modeDonor[mode] = m;
        }
        if (!modeDonor.ContainsKey("translucent") && modeDonor.ContainsKey("mask")) modeDonor["translucent"] = modeDonor["mask"];
        if (!modeDonor.ContainsKey("mask")) modeDonor["mask"] = donorMat;
        if (!modeDonor.ContainsKey("translucent")) modeDonor["translucent"] = donorMat;
        Console.WriteLine("donors: " + string.Join(", ", modeDonor.Select(kv => kv.Key + "=" + kv.Value.Name)));
        return modeDonor;
    }

    static Bntx.BntxFile TextureArchive(ResFile res)
    {
        // BfresLibrary keeps the embedded BNTX parsed in LoadedFileData and saves it again from there.
        var ext = res.ExternalFiles.Values.FirstOrDefault(e => e.LoadedFileData is Bntx.BntxFile)
                  ?? throw new Exception("the model has no embedded texture archive");
        return (Bntx.BntxFile)ext.LoadedFileData;
    }

    static byte[] TexturePixels(JToken t, Material donorMat)
    {
        // {"neutral_light": [brightness, alpha]} makes a 16x16 baked-light texture whose colour times the donor's
        // gsys_bake_light_scale is grey: World 1's grass scales light by (0.78, 1.17, 0.71), which would tint white
        // eggs and castles green.
        uint w = (uint)t["width"], h = (uint)t["height"];
        if (t["neutral_light"] == null) return File.ReadAllBytes((string)(t["bc1"] ?? t["rgba"]));
        float bright = (float)t["neutral_light"][0];
        var scale = donorMat != null && donorMat.ShaderParams.ContainsKey("gsys_bake_light_scale")
            ? (float[])donorMat.ShaderParams["gsys_bake_light_scale"].DataValue : new float[] { 1, 1, 1 };
        var px = new byte[4];
        for (int k = 0; k < 3; k++) px[k] = (byte)Math.Max(0, Math.Min(255, Math.Round(255 * bright / Math.Max(scale[k], 1e-3f))));
        px[3] = (byte)(int)t["neutral_light"][1];
        var rgba = new byte[w * h * 4];
        for (int i = 0; i < w * h; i++) System.Buffer.BlockCopy(px, 0, rgba, i * 4, 4);
        return rgba;
    }

    // New textures, cloning their settings from an existing one: [{name, width, height, rgba | bc1 | neutral_light}]
    static void AddTextures(ResFile res, JToken textures, Material donorMat)
    {
        if (textures == null) return;
        var bntx = TextureArchive(res);
        var like = bntx.Textures[0];
        foreach (var t in textures)
        {
            string name = (string)t["name"];
            if (bntx.TextureDict.ContainsKey(name)) throw new Exception("texture already exists: " + name);
            var tex = NewTexture(like, name, (uint)t["width"], (uint)t["height"], TexturePixels(t, donorMat), t["bc1"] != null, (int?)t["mips"] ?? 1);
            bntx.Textures.Add(tex);
            bntx.TextureDict.Add(name);
            res.Textures.Add(name, new BfresLibrary.Switch.SwitchTexture(bntx, tex));
        }
    }

    // Parts appended to a model, each with its own copy of a donor material (by "mode") in the donor's vertex layout:
    //   {name, material_name, vertices (float32: px py pz nx ny nz u v u1 v1 r g b a per vertex), vertex_count,
    //    indices, index_format u16|u32, mode, wrap [u, v], textures {albedo, specular, bake0, bake1, edge_mask, ...}}
    // A part with "route": true copies the route donor instead; its vertices have 10 more floats (tangent xyzw,
    // binormal xyzw, mask uv) and "mask_offset" is its texmtx0 translate v (hidden; NSMBW's mask SRT, -2.9...).
    static void AddParts(ResFile res, Model model, List<JObject> parts, Dictionary<string, Material> modeDonor, Shape donor,
                         Shape routeDonor = null)
    {
        var matFiles = new Dictionary<string, string>();       // a deep copy per part: Export once per donor + Import
        foreach (var kv in modeDonor)
        {
            matFiles[kv.Key] = Tmp() + "_" + kv.Key + ".bfmat";
            kv.Value.Export(matFiles[kv.Key], res);
        }
        foreach (var part in parts)
        {
            var mat = new Material();
            bool route = (bool?)part["route"] == true;
            if (route && (routeDonor == null || !matFiles.ContainsKey("route")))
                throw new Exception("part " + (string)part["name"] + " is a route but the template has no route material");
            string pmode = route ? "route" : (string)part["mode"] ?? "opaque";
            mat.Import(matFiles.ContainsKey(pmode) ? matFiles[pmode] : matFiles["opaque"], res);
            mat.Name = (string)part["material_name"];
            if (model.Materials.ContainsKey(mat.Name)) throw new Exception("material already exists: " + mat.Name);
            if (route && part["mask_offset"] != null)
            {
                var prm = mat.ShaderParams["texmtx0"];
                var srt = (TexSrt)prm.DataValue;
                srt.Translation = new Vector2F(srt.Translation.X, (float)part["mask_offset"]);
                prm.DataValue = srt;
            }
            var texmap = (JObject)part["textures"];
            // "wrap": [u, v] of repeat / clamp / mirror on the albedo's sampler (NSMBW clamps its path and mushroom
            // textures across; the donor repeats). Samplers pair with texture refs by index.
            var wrap = part["wrap"] as JArray;
            for (int ti = 0; ti < mat.TextureRefs.Count && wrap != null; ti++)
                if (mat.TextureRefs[ti].Name.EndsWith("_alb") && ti < mat.Samplers.Count)
                {
                    var ts = mat.Samplers[ti].TexSampler;
                    ts.ClampX = Clamp((string)wrap[0]);
                    ts.ClampY = Clamp((string)wrap[1]);
                }
            foreach (var tr in mat.TextureRefs)
            {
                if (tr.Name.EndsWith("_alb")) tr.Name = (string)texmap["albedo"];
                else if (tr.Name.EndsWith("_spm") && texmap["specular"] != null) tr.Name = (string)texmap["specular"];
                // Bake_<model>_g00_b00 is the ambient occlusion (one channel), _b01 the baked light (rgb) and its alpha
                else if (tr.Name.StartsWith("Bake_") && (texmap["bake0"] ?? texmap["bake"]) != null)
                    tr.Name = (string)((tr.Name.Contains("_b01") ? texmap["bake1"] : texmap["bake0"]) ?? texmap["bake"]);
                else if (tr.Name.StartsWith("lmDif") && texmap["lightmap_diffuse"] != null) tr.Name = (string)texmap["lightmap_diffuse"];
                else if (tr.Name.StartsWith("lmSpc") && texmap["lightmap_specular"] != null) tr.Name = (string)texmap["lightmap_specular"];
                else if (tr.Name.EndsWith("_msk") && texmap["edge_mask"] != null) tr.Name = (string)texmap["edge_mask"];
                else if (tr.Name.EndsWith("_nml") && texmap["normal"] != null) tr.Name = (string)texmap["normal"];
            }
            model.Materials.Add(mat.Name, mat);
            int n = (int)part["vertex_count"];
            float[] v = ReadFloats((string)part["vertices"]);
            int S = route ? 24 : 14;
            var shapeDonor = route ? routeDonor : donor;
            var donorVb = shapeDonor.VertexBuffer;
            Vector4F[] P = new Vector4F[n], N = new Vector4F[n], U0 = new Vector4F[n], U1 = new Vector4F[n], C = new Vector4F[n];
            Vector4F[] T = new Vector4F[n], B = new Vector4F[n], U2 = new Vector4F[n];
            float[] lo = { float.MaxValue, float.MaxValue, float.MaxValue }, hi = { float.MinValue, float.MinValue, float.MinValue };
            for (int i = 0; i < n; i++)
            {
                int o = i * S;
                P[i] = new Vector4F(v[o], v[o + 1], v[o + 2], 1); N[i] = new Vector4F(v[o + 3], v[o + 4], v[o + 5], 0);
                U0[i] = new Vector4F(v[o + 6], v[o + 7], 0, 0); U1[i] = new Vector4F(v[o + 8], v[o + 9], 0, 0);
                C[i] = new Vector4F(v[o + 10], v[o + 11], v[o + 12], v[o + 13]);
                if (route)
                {
                    T[i] = new Vector4F(v[o + 14], v[o + 15], v[o + 16], v[o + 17]);
                    B[i] = new Vector4F(v[o + 18], v[o + 19], v[o + 20], v[o + 21]);
                    U2[i] = new Vector4F(v[o + 22], v[o + 23], 0, 0);
                }
                for (int k = 0; k < 3; k++) { lo[k] = Math.Min(lo[k], v[o + k]); hi[k] = Math.Max(hi[k], v[o + k]); }
            }
            var helper = new VertexBufferHelper { ByteOrder = ByteOrder.LittleEndian, Attributes = new List<VertexBufferHelperAttrib>() };
            var data = new Dictionary<string, Vector4F[]> { ["_p0"] = P, ["_n0"] = N, ["_c0"] = C, ["_u0"] = U0, ["_u1"] = U1,
                                                            ["_t0"] = T, ["_b0"] = B, ["_u2"] = U2 };
            foreach (var kv in donorVb.Attributes)                    // same names, formats and order as the vanilla buffer
                helper.Attributes.Add(new VertexBufferHelperAttrib { Name = kv.Key, Format = kv.Value.Format, Data = data[kv.Key] });
            var vb = helper.ToVertexBuffer();
            var ib = File.ReadAllBytes((string)part["indices"]);
            bool wide = (string)part["index_format"] == "u32";
            var idx = new uint[ib.Length / (wide ? 4 : 2)];
            for (int i = 0; i < idx.Length; i++) idx[i] = wide ? BitConverter.ToUInt32(ib, i * 4) : BitConverter.ToUInt16(ib, i * 2);
            var mesh = new Mesh { PrimitiveType = GX2PrimitiveType.Triangles };
            mesh.SetIndices(idx, wide ? GX2IndexFormat.UInt32LittleEndian : GX2IndexFormat.UInt16LittleEndian);
            mesh.SubMeshes.Add(new SubMesh { Offset = 0, Count = (uint)idx.Length });
            Shape shape = CopyShape(shapeDonor);
            shape.Name = (string)part["name"];
            if (model.Shapes.ContainsKey(shape.Name)) throw new Exception("shape already exists: " + shape.Name);
            shape.MaterialIndex = (ushort)(model.Materials.Count - 1); shape.BoneIndex = 0;
            shape.VertexBufferIndex = (ushort)model.VertexBuffers.Count; shape.VertexSkinCount = 0;
            shape.VertexBuffer = vb;
            shape.Meshes = new List<Mesh> { mesh };
            var center = new Vector3F((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2);
            var extent = new Vector3F(Math.Max(1, (hi[0] - lo[0]) / 2), Math.Max(1, (hi[1] - lo[1]) / 2), Math.Max(1, (hi[2] - lo[2]) / 2));
            shape.SubMeshBoundings = new List<Bounding> { new Bounding { Center = center, Extent = extent }, new Bounding { Center = center, Extent = extent } };
            shape.RadiusArray = new List<float> { (float)Math.Sqrt(extent.X * extent.X + extent.Y * extent.Y + extent.Z * extent.Z) };
            shape.KeyShapes = new ResDict<KeyShape>();
            model.VertexBuffers.Add(vb);
            model.Shapes.Add(shape.Name, shape);
        }
        foreach (var f in matFiles.Values) File.Delete(f);
    }

    // ---- edit: change a native world's models, keeping everything else ----------------------------------------------
    //   spec: input, output,
    //         remove_shapes [{model, shape}]                       shapes taken out (their buffers stay, unused)
    //         replace_textures [{name, width, height, rgba | bc1}]  a texture's pixels replaced (any size)
    //         textures [...] and add {model, parts [...]}           new textures, and parts appended to a model
    static void EditWorld(JObject spec)
    {
        var res = new ResFile((string)spec["input"]);
        foreach (var r in (JArray)spec["remove_shapes"] ?? new JArray())
        {
            string mname = (string)r["model"], sname = (string)r["shape"];
            if (!res.Models.ContainsKey(mname) || !res.Models[mname].Shapes.ContainsKey(sname))
                throw new Exception("no shape " + sname + " in model " + mname);
            res.Models[mname].Shapes.RemoveKey(sname);
        }
        var replace = (JArray)spec["replace_textures"];
        if (replace != null && replace.Count > 0)
        {
            var bntx = TextureArchive(res);
            foreach (var t in replace)
            {
                string name = (string)t["name"];
                int i = bntx.Textures.Select((x, k) => x.Name == name ? k : -1).FirstOrDefault(k => k >= 0, -1);
                if (i < 0) throw new Exception("no texture " + name);
                var tex = NewTexture(bntx.Textures[i], name, (uint)t["width"], (uint)t["height"], TexturePixels(t, null), t["bc1"] != null, (int?)t["mips"] ?? 1);
                bntx.Textures[i] = tex;
                res.Textures.RemoveKey(name);
                res.Textures.Add(name, new BfresLibrary.Switch.SwitchTexture(bntx, tex));
            }
        }
        var add = spec["add"] as JObject;
        bool adding = add != null && ((JArray)add["parts"]).Count > 0;
        Dictionary<string, Material> modeDonor = null;
        Shape donor = null;
        if (adding)
        {
            string mname = (string)add["model"];
            if (!res.Models.ContainsKey(mname)) throw new Exception("no model " + mname);
            modeDonor = Donors(res.Models[mname], mname, out donor);
        }
        AddTextures(res, spec["textures"], modeDonor?["opaque"]);
        foreach (var e in spec["shape_edits"] as JArray ?? new JArray()) EditShape(res, (JObject)e);
        if (adding)
            AddParts(res, res.Models[(string)add["model"]], ((JArray)add["parts"]).Cast<JObject>().ToList(), modeDonor, donor);
        res.Save((string)spec["output"]);
    }

    // rename <in> <old> <new> [out]: every model, texture (and texture reference), material and shape whose name
    // contains <old> gets <new> instead (a tileset copied for another level: Pa1_1-1_1 -> Pa1_1-6_1), with the
    // dictionaries rebuilt by BfresLibrary.
    static void Rename(string input, string from, string to, string output)
    {
        var res = new ResFile(input);
        string R(string n) => n.Replace(from, to);
        foreach (var model in res.Models.Values.ToList())
        {
            foreach (var mat in model.Materials.Values.ToList())
            {
                foreach (var tr in mat.TextureRefs) tr.Name = R(tr.Name);
                if (mat.Name.Contains(from)) { model.Materials.RemoveKey(mat.Name); mat.Name = R(mat.Name); model.Materials.Add(mat.Name, mat); }
            }
            foreach (var sh in model.Shapes.Values.ToList())
                if (sh.Name.Contains(from)) { model.Shapes.RemoveKey(sh.Name); sh.Name = R(sh.Name); model.Shapes.Add(sh.Name, sh); }
            if (model.Name.Contains(from)) { res.Models.RemoveKey(model.Name); model.Name = R(model.Name); res.Models.Add(model.Name, model); }
        }
        var ext = res.ExternalFiles.Values.FirstOrDefault(e => e.LoadedFileData is Bntx.BntxFile);
        if (ext != null)
        {
            var bntx = (Bntx.BntxFile)ext.LoadedFileData;
            for (int i = 0; i < bntx.Textures.Count; i++)
            {
                var t = bntx.Textures[i];
                if (!t.Name.Contains(from)) continue;
                string old = t.Name;
                t.Name = R(old);
                bntx.TextureDict[i] = t.Name;
                if (res.Textures.ContainsKey(old)) res.Textures.RemoveKey(old);
                res.Textures.Add(t.Name, new BfresLibrary.Switch.SwitchTexture(bntx, t));
            }
        }
        res.Save(output);
    }

    // shape_edits [{model, shape, matrices [[16 floats, row-major]...], per_slot, texture, replace}]:
    //   matrices: in the space the shape's vertices are stored in (its bone's for skin count 0, each skin slot's bone's
    //   for rigid skinning with per_slot, the model's for smooth skinning). Positions, normals, tangents and bitangents
    //   are moved, a mirroring matrix reverses the triangles, and the culling bounds are remade.
    //   texture: the shape gets its own copy of its material whose texture `replace` (its albedo) becomes `texture`.
    static void EditShape(ResFile res, JObject e)
    {
        string mname = (string)e["model"], sname = (string)e["shape"];
        if (!res.Models.ContainsKey(mname) || !res.Models[mname].Shapes.ContainsKey(sname))
            throw new Exception("no shape " + sname + " in model " + mname);
        var model = res.Models[mname];
        var shape = model.Shapes[sname];
        var mats = (e["matrices"] as JArray)?.Select(m => ((JArray)m).Select(x => (float)x).ToArray()).ToList();
        if (mats != null && mats.Count > 0)
        {
            if (model.Shapes.Values.Count(s => s.VertexBufferIndex == shape.VertexBufferIndex) > 1)
                throw new Exception("shape " + sname + " shares its vertex buffer with another shape; it cannot be moved alone");
            bool perSlot = (bool?)e["per_slot"] ?? false;
            var h = new VertexBufferHelper(shape.VertexBuffer, ByteOrder.LittleEndian);
            var slots = perSlot ? h.Attributes.FirstOrDefault(a => a.Name == "_i0")?.Data : null;
            int n = h.Attributes.Count > 0 ? h.Attributes[0].Data.Length : 0;
            float[] Pick(int i) => mats[slots == null ? 0 : Math.Max(0, Math.Min(mats.Count - 1, (int)Math.Round(slots[i].X)))];
            float[] lo = { float.MaxValue, float.MaxValue, float.MaxValue }, hi = { float.MinValue, float.MinValue, float.MinValue };
            bool mirror = false;
            foreach (var a in h.Attributes)
            {
                char kind = a.Name.Length > 1 ? a.Name[1] : ' ';
                if (kind != 'p' && kind != 'n' && kind != 't' && kind != 'b') continue;
                for (int i = 0; i < a.Data.Length; i++)
                {
                    float[] m = Pick(i);
                    var v = a.Data[i];
                    if (kind == 'p')
                    {
                        var p = new Vector4F(m[0] * v.X + m[1] * v.Y + m[2] * v.Z + m[3], m[4] * v.X + m[5] * v.Y + m[6] * v.Z + m[7],
                                             m[8] * v.X + m[9] * v.Y + m[10] * v.Z + m[11], v.W);
                        a.Data[i] = p;
                        lo[0] = Math.Min(lo[0], p.X); lo[1] = Math.Min(lo[1], p.Y); lo[2] = Math.Min(lo[2], p.Z);
                        hi[0] = Math.Max(hi[0], p.X); hi[1] = Math.Max(hi[1], p.Y); hi[2] = Math.Max(hi[2], p.Z);
                        continue;
                    }
                    // normals by the inverse transpose of the 3x3, tangents/bitangents by the 3x3 itself
                    float a0 = m[0], a1 = m[1], a2 = m[2], b0 = m[4], b1 = m[5], b2 = m[6], c0 = m[8], c1 = m[9], c2 = m[10];
                    float det = a0 * (b1 * c2 - b2 * c1) - a1 * (b0 * c2 - b2 * c0) + a2 * (b0 * c1 - b1 * c0);
                    if (i == 0 && kind == 'n') mirror = det < 0;
                    float x, y, z;
                    if (kind == 'n')
                    {
                        // cofactor matrix = det * inverse transpose; the scale goes with the normalisation
                        x = (b1 * c2 - b2 * c1) * v.X + (b2 * c0 - b0 * c2) * v.Y + (b0 * c1 - b1 * c0) * v.Z;
                        y = (a2 * c1 - a1 * c2) * v.X + (a0 * c2 - a2 * c0) * v.Y + (a1 * c0 - a0 * c1) * v.Z;
                        z = (a1 * b2 - a2 * b1) * v.X + (a2 * b0 - a0 * b2) * v.Y + (a0 * b1 - a1 * b0) * v.Z;
                        if (det < 0) { x = -x; y = -y; z = -z; }
                    }
                    else
                    {
                        x = a0 * v.X + a1 * v.Y + a2 * v.Z; y = b0 * v.X + b1 * v.Y + b2 * v.Z; z = c0 * v.X + c1 * v.Y + c2 * v.Z;
                    }
                    float len = (float)Math.Sqrt(x * x + y * y + z * z);
                    if (len > 1e-12f) { x /= len; y /= len; z /= len; }
                    a.Data[i] = new Vector4F(x, y, z, v.W);
                }
            }
            if (!h.Attributes.Any(a => a.Name.StartsWith("_n")))
            {
                float[] m = mats[0];
                mirror = m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8]) + m[2] * (m[4] * m[9] - m[5] * m[8]) < 0;
            }
            var vb = h.ToVertexBuffer();
            shape.VertexBuffer = vb;
            model.VertexBuffers[shape.VertexBufferIndex] = vb;
            if (mirror)
                foreach (var mesh in shape.Meshes)
                {
                    if (mesh.PrimitiveType != GX2PrimitiveType.Triangles) continue;
                    var idx = mesh.GetIndices().ToList();
                    for (int i = 0; i + 2 < idx.Count; i += 3) { var t = idx[i + 1]; idx[i + 1] = idx[i + 2]; idx[i + 2] = t; }
                    mesh.SetIndices(idx, mesh.IndexFormat);
                }
            if (n > 0 && lo[0] <= hi[0])
            {
                var center = new Vector3F((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2);
                var extent = new Vector3F(Math.Max(0.5f, (hi[0] - lo[0]) / 2), Math.Max(0.5f, (hi[1] - lo[1]) / 2), Math.Max(0.5f, (hi[2] - lo[2]) / 2));
                float radius = (float)Math.Sqrt(extent.X * extent.X + extent.Y * extent.Y + extent.Z * extent.Z);
                int nb = Math.Max(1, shape.SubMeshBoundings?.Count ?? 0);
                shape.SubMeshBoundings = Enumerable.Range(0, nb).Select(_ => new Bounding { Center = center, Extent = extent }).ToList();
                shape.RadiusArray = Enumerable.Range(0, Math.Max(1, shape.RadiusArray?.Count ?? 0)).Select(_ => radius).ToList();
                if (shape.BoundingRadiusList != null && shape.BoundingRadiusList.Count > 0)
                    shape.BoundingRadiusList = shape.BoundingRadiusList.Select(_ => new Vector4F(center.X, center.Y, center.Z, radius)).ToList();
            }
        }
        var tex = (string)e["texture"];
        if (!string.IsNullOrEmpty(tex))
        {
            var src = model.Materials[shape.MaterialIndex];
            string file = Tmp() + ".bfmat";
            src.Export(file, res);
            var mat = new Material();
            mat.Import(file, res);
            File.Delete(file);
            int k = 0;
            do mat.Name = src.Name + "_kdx" + (k++).ToString("D2"); while (model.Materials.ContainsKey(mat.Name));
            string replace = (string)e["replace"];
            var refs = mat.TextureRefs.Where(t => t.Name == replace).ToList();
            if (refs.Count == 0) refs = mat.TextureRefs.Where(t => t.Name.EndsWith("_alb")).Take(1).ToList();
            if (refs.Count == 0) throw new Exception("material " + src.Name + " of " + sname + " has no albedo texture to replace");
            foreach (var t in refs) t.Name = tex;
            model.Materials.Add(mat.Name, mat);
            shape.MaterialIndex = (ushort)(model.Materials.Count - 1);
        }
    }

    // BfresLibrary saves a Switch sampler's wrap from TexSampler.ClampX/Y: Wrap -> Repeat, Mirror -> Mirror,
    // ClampBorder -> ClampToEdge (the Wii's GX_CLAMP)
    static GX2TexClamp Clamp(string w) =>
        w == "clamp" ? GX2TexClamp.ClampBorder : w == "mirror" ? GX2TexClamp.Mirror : GX2TexClamp.Wrap;

    // ---- build --------------------------------------------------------------------------------------------------------
    //   spec: template, output, marker_model, terrain_model, keep_models[],
    //         terrain {vertices (float32: px py pz nx ny nz u v u1 v1 r g b a per vertex), vertex_count, indices (uint16),
    //                  textures {albedo, specular, bake}, material_name},
    //         textures [{name, width, height, rgba}], points [{name, position [x,y,z], marker}], marker_shapes {kind: shape}
    static void BuildWorld(JObject spec)
    {
        var res = new ResFile((string)spec["template"]);
        string markerName = (string)spec["marker_model"], terrainName = (string)spec["terrain_model"];
        var keep = new HashSet<string>(spec["keep_models"].Select(x => (string)x));

        // 1. Only the map-point model and the terrain stay; animations belonged to the removed scenery.
        //    keep_extras: the template's other models stay too (the game's actors build from them: W6a's CS_W6a_Trap,
        //    W8a's dark cloud binds CS_W8a_DarkCloud* from the world's resource, the decoration models), hidden (every
        //    bone invisible), with their animations; only the models and animations named after a vanilla route
        //    (R + 8 characters, plus _fts / _fcl) go. A full port replaces every world, so nothing vanilla is left.
        bool keepExtras = spec["keep_extras"] != null && (bool)spec["keep_extras"];
        var routeName = new System.Text.RegularExpressions.Regex(@"^R\w{8}(_f\w\w)?$");
        var dropModels = spec["drop_models"] != null ? new HashSet<string>(spec["drop_models"].Select(x => (string)x)) : new HashSet<string>();
        foreach (var key in res.Models.Keys.ToList())
            if (!keep.Contains(key) && (!keepExtras || routeName.IsMatch(key) || dropModels.Contains(key))) res.Models.RemoveKey(key);
        if (keepExtras)
        {
            foreach (var key in res.SkeletalAnims.Keys.ToList()) if (routeName.IsMatch(key)) res.SkeletalAnims.RemoveKey(key);
            foreach (var key in res.MaterialAnims.Keys.ToList()) if (routeName.IsMatch(key)) res.MaterialAnims.RemoveKey(key);
            foreach (var key in res.BoneVisibilityAnims.Keys.ToList()) if (routeName.IsMatch(key)) res.BoneVisibilityAnims.RemoveKey(key);
            foreach (var key in res.ShapeAnims.Keys.ToList()) if (routeName.IsMatch(key)) res.ShapeAnims.RemoveKey(key);
            foreach (var m in res.Models.Values)
                if (!keep.Contains(m.Name))
                    foreach (var bone in m.Skeleton.Bones.Values) bone.Flags &= ~BfresLibrary.BoneFlags.Visible;   // (BfresLibrary's Visible setter keeps the bit)
            Console.WriteLine("kept extras: " + string.Join(", ", res.Models.Keys.Where(k => !keep.Contains(k))));
        }
        else
        {
            res.SkeletalAnims.Clear(); res.MaterialAnims.Clear(); res.BoneVisibilityAnims.Clear(); res.ShapeAnims.Clear(); res.SceneAnims.Clear();
        }

        // 2. The ground material every part copies (Donors), 3. the new textures, 4. one shape per part.
        //    A painted 2D map is one part; an imported 3D model is one part per material.
        var terrain = res.Models[terrainName];
        var modeDonor = Donors(terrain, terrainName, out Shape donor);
        var routeDonor = RouteDonor(terrain, out Material routeMat);
        if (routeMat != null) modeDonor["route"] = routeMat;
        AddTextures(res, spec["textures"], modeDonor["opaque"]);
        var tspec = (JObject)spec["terrain"];
        var parts = tspec["parts"] != null ? tspec["parts"].Cast<JObject>().ToList()
                                           : new List<JObject> { new JObject(tspec) { ["name"] = "kdx_terrain" } };
        terrain.Shapes.Clear(); terrain.Materials.Clear(); terrain.VertexBuffers.Clear();
        AddParts(res, terrain, parts, modeDonor, donor, routeDonor);
        // 5. Map points: one bone per point (a copy of a vanilla point bone) and a copy of a vanilla marker shape.
        var marker = res.Models[markerName];
        var sk = marker.Skeleton;
        var templates = new Dictionary<string, Shape>();
        foreach (var kv in (JObject)spec["marker_shapes"])
            templates[kv.Key] = CopyShape(marker.Shapes[(string)kv.Value]);
        var templateBone = sk.Bones.Values.First(b => b.ParentIndex == 0);
        var root = sk.Bones.Values.First(b => b.ParentIndex < 0);
        var rootName = sk.Bones.Keys.First(k => sk.Bones[k] == root);
        sk.Bones.Clear();
        sk.Bones.Add(rootName, root);
        marker.Shapes.Clear(); marker.VertexBuffers.Clear();
        foreach (var p in spec["points"])
        {
            string name = (string)p["name"];
            var pos = p["position"];
            var bone = new Bone
            {
                Name = name, ParentIndex = 0, SmoothMatrixIndex = -1, RigidMatrixIndex = -1, BillboardIndex = templateBone.BillboardIndex,
                Flags = templateBone.Flags, FlagsRotation = templateBone.FlagsRotation, FlagsBillboard = templateBone.FlagsBillboard,
                FlagsTransform = templateBone.FlagsTransform, FlagsTransformCumulative = templateBone.FlagsTransformCumulative,
                Scale = new Vector3F(1, 1, 1), Rotation = new Vector4F(0, 0, 0, 1),
                Position = new Vector3F((float)pos[0], (float)pos[1], (float)pos[2]),
            };
            sk.Bones.Add(name, bone);
            string kind = (string)p["marker"];
            if (kind == null || !templates.ContainsKey(kind)) continue;
            var s = CopyShape(templates[kind]);
            s.Name = name + "__mtCourseOBJ";
            s.BoneIndex = (ushort)(sk.Bones.Count - 1);
            s.MaterialIndex = 0;
            s.VertexBufferIndex = (ushort)marker.VertexBuffers.Count;
            marker.VertexBuffers.Add(s.VertexBuffer);
            marker.Shapes.Add(s.Name, s);
        }
        res.Save((string)spec["output"]);
    }
    // ---- marker -------------------------------------------------------------------------------------------------------
    //   spec: input, output, marker_model, points [{name, position [x,y,z] (root space), marker (kind or null)}],
    //         marker_shapes {kind: shape}
    // Rebuilds only the map-point model's skeleton in the given order (all bones flat under the root, as "build" does):
    // a bone that already exists keeps its marker shapes, a new one gets a copy of the kind's vanilla marker. Every
    // other model, material, texture and animation of the file stays as it is.
    static void RebuildMarker(JObject spec)
    {
        var res = new ResFile((string)spec["input"]);
        var marker = res.Models[(string)spec["marker_model"]];
        var sk = marker.Skeleton;
        var oldNames = sk.Bones.Keys.ToList();
        var shapesOf = new Dictionary<string, List<Shape>>();
        foreach (var s in marker.Shapes.Values)
        {
            string bn = s.BoneIndex < oldNames.Count ? oldNames[s.BoneIndex] : null;
            if (bn == null) continue;
            if (!shapesOf.ContainsKey(bn)) shapesOf[bn] = new List<Shape>();
            shapesOf[bn].Add(CopyShape(s));
        }
        var templates = new Dictionary<string, Shape>();
        foreach (var kv in (JObject)spec["marker_shapes"])
            templates[kv.Key] = CopyShape(marker.Shapes[(string)kv.Value]);
        var templateBone = sk.Bones.Values.First(b => b.ParentIndex == 0);
        var root = sk.Bones.Values.First(b => b.ParentIndex < 0);
        var rootName = sk.Bones.Keys.First(k => sk.Bones[k] == root);
        sk.Bones.Clear();
        sk.Bones.Add(rootName, root);
        marker.Shapes.Clear(); marker.VertexBuffers.Clear();
        void Attach(Shape s, string name)
        {
            s.BoneIndex = (ushort)sk.Bones.Keys.ToList().IndexOf(name);
            s.VertexBufferIndex = (ushort)marker.VertexBuffers.Count;
            marker.VertexBuffers.Add(s.VertexBuffer);
            marker.Shapes.Add(s.Name, s);
        }
        if (shapesOf.ContainsKey(rootName)) foreach (var s in shapesOf[rootName]) Attach(s, rootName);
        foreach (var p in spec["points"])
        {
            string name = (string)p["name"];
            var pos = p["position"];
            sk.Bones.Add(name, new Bone
            {
                Name = name, ParentIndex = 0, SmoothMatrixIndex = -1, RigidMatrixIndex = -1, BillboardIndex = templateBone.BillboardIndex,
                Flags = templateBone.Flags, FlagsRotation = templateBone.FlagsRotation, FlagsBillboard = templateBone.FlagsBillboard,
                FlagsTransform = templateBone.FlagsTransform, FlagsTransformCumulative = templateBone.FlagsTransformCumulative,
                Scale = new Vector3F(1, 1, 1), Rotation = new Vector4F(0, 0, 0, 1),
                Position = new Vector3F((float)pos[0], (float)pos[1], (float)pos[2]),
            });
            if (shapesOf.ContainsKey(name)) { foreach (var s in shapesOf[name]) Attach(s, name); continue; }
            string kind = (string)p["marker"];
            if (kind == null || !templates.ContainsKey(kind)) continue;
            var c = CopyShape(templates[kind]);
            c.Name = name + "__mtCourseOBJ";
            Attach(c, name);
        }
        res.Save((string)spec["output"]);
    }
    // ---- routecopy ----------------------------------------------------------------------------------------------------
    //   spec: input, output, copies [{source, target, offset [x,y,z]}]
    // A path's appear animation is a model named like the path (R<from><to>) whose root bone is "r" + the name without
    // the R, a skeletal animation of the same name and optional material animations <name>_fts / <name>_fcl. Each copy
    // duplicates them under the target path's name and moves the root bone (model rest pose and animation base pose)
    // by offset. Nothing else in the file changes.
    static void RouteCopy(JObject spec)
    {
        var res = new ResFile((string)spec["input"]);
        string tmp = Path.Combine(Path.GetTempPath(), "kdx_routecopy_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tmp);
        try
        {
            foreach (var c in (JArray)spec["copies"])
            {
                string src = (string)c["source"], dst = (string)c["target"];
                var off = c["offset"] ?? new JArray(0, 0, 0);
                float ox = (float)off[0], oy = (float)off[1], oz = (float)off[2];
                if (!res.Models.ContainsKey(src)) throw new Exception("no model " + src);
                if (res.Models.ContainsKey(dst)) throw new Exception("model " + dst + " already exists");
                string srcRoot = "r" + src.Substring(1), dstRoot = "r" + dst.Substring(1);
                string f = Path.Combine(tmp, src + ".bfmdl");
                res.Models[src].Export(f, res);
                var m = new Model(); m.Import(f, res); m.Name = dst;
                var sk = m.Skeleton;
                var rootKey = sk.Bones.Keys.First(k => sk.Bones[k].ParentIndex < 0);
                var root = sk.Bones[rootKey];
                root.Position = new Vector3F(root.Position.X + ox, root.Position.Y + oy, root.Position.Z + oz);
                if (rootKey == srcRoot) { sk.Bones.Rename(srcRoot, dstRoot); root.Name = dstRoot; }
                res.Models.Add(dst, m);
                if (res.SkeletalAnims.ContainsKey(src))
                {
                    string fa = Path.Combine(tmp, src + ".bfska");
                    res.SkeletalAnims[src].Export(fa, res);
                    var a = new SkeletalAnim(); a.Import(fa, res); a.Name = dst;
                    foreach (var ba in a.BoneAnims)
                    {
                        if (ba.Name != rootKey) continue;
                        var bd = ba.BaseData;
                        bd.Translate = new Vector3F(bd.Translate.X + ox, bd.Translate.Y + oy, bd.Translate.Z + oz);
                        ba.BaseData = bd;
                        if (rootKey == srcRoot) ba.Name = dstRoot;
                    }
                    res.SkeletalAnims.Add(dst, a);
                }
                // the Switch loader files material animations by name (an _fts lands in ShaderParamAnims): look in all
                var dicts = new[] { res.ShaderParamAnims, res.TexSrtAnims, res.ColorAnims, res.TexPatternAnims, res.MatVisibilityAnims };
                foreach (var suffix in new[] { "_fts", "_fcl", "_ftp", "_fsp", "_fvs" })
                foreach (var dict in dicts)
                {
                    if (!dict.ContainsKey(src + suffix)) continue;
                    string fm = Path.Combine(tmp, src + suffix + ".bfmaa");
                    dict[src + suffix].Export(fm, res);
                    var ma = new MaterialAnim(); ma.Import(fm, res); ma.Name = dst + suffix;
                    dict.Add(dst + suffix, ma);
                }
            }
            res.Save((string)spec["output"]);
        }
        finally { try { Directory.Delete(tmp, true); } catch { } }
    }
    // ---- routemake ----------------------------------------------------------------------------------------------------
    //   spec: input, output, items [{target, source_model, shapes [names], keys {scale: [[frame, x, y, z]...],
    //         rotate: [[frame, x, y, z]...] (degrees, Euler XYZ), translate: [[frame, x, y, z]...],
    //         interp: {scale|rotate|translate: "linear" (default) | "smooth"}}, frames, pivot [x,y,z]}]
    // Moves the listed shapes (unskinned, on the model's root bone at the origin) out of source_model into a new model
    // named like the path, with bones root "r<name>" <- kdx_pivot (at the pivot) <- kdx_appear (animated) <- kdx_inner
    // (back at the origin, holding the shapes): scaling happens around the pivot and the vertices stay as they are.
    // A skeletal animation of the same name (cubic curves on kdx_appear) is what the game plays when the path opens.
    // Cubic curves as the game's own bone animations use: per segment k0 + k1 t + k2 t^2 + k3 t^3 (t = 0..1).
    // "linear": k = [v0, d, 0, 0]; "smooth" (eases in and out of every key): k = [v0, 0, 3d, -2d].
    static AnimCurve KeyCurve(uint target, List<float> frames, List<float> values, bool smooth)
    {
        var c = new AnimCurve
        {
            CurveType = AnimCurveType.Cubic, FrameType = AnimCurveFrameType.Single, KeyType = AnimCurveKeyType.Single,
            AnimDataOffset = target, StartFrame = frames[0], EndFrame = frames[frames.Count - 1], Scale = 1f, Delta = 0f,
            Frames = frames.ToArray(), Keys = new float[frames.Count, 4],
        };
        c.Offset = 0f;
        for (int i = 0; i < frames.Count; i++)
        {
            float d = i + 1 < frames.Count ? values[i + 1] - values[i] : 0f;
            c.Keys[i, 0] = values[i];
            c.Keys[i, 1] = smooth ? 0f : d;
            c.Keys[i, 2] = smooth ? 3f * d : 0f;
            c.Keys[i, 3] = smooth ? -2f * d : 0f;
        }
        return c;
    }

    static void RouteMake(JObject spec)
    {
        var res = new ResFile((string)spec["input"]);
        string tmp = Path.Combine(Path.GetTempPath(), "kdx_routemake_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tmp);
        var flagsScale = res.SkeletalAnims.Values.Select(x => x.FlagsScale).FirstOrDefault();
        try
        {
            foreach (var it in (JArray)spec["items"])
            {
                string dst = (string)it["target"], srcName = (string)it["source_model"];
                var names = new HashSet<string>(((JArray)it["shapes"]).Select(x => (string)x));
                if (res.Models.ContainsKey(dst)) throw new Exception("model " + dst + " already exists");
                var src = res.Models[srcName];
                foreach (var n in names)
                {
                    if (!src.Shapes.ContainsKey(n)) throw new Exception("no shape " + n + " in " + srcName);
                    var sh = src.Shapes[n];
                    if (sh.VertexSkinCount != 0) throw new Exception("shape " + n + " is skinned");
                    var bn = src.Skeleton.Bones[sh.BoneIndex];
                    if (bn.ParentIndex >= 0 || Math.Abs(bn.Position.X) + Math.Abs(bn.Position.Y) + Math.Abs(bn.Position.Z) > 1e-4f)
                        throw new Exception("shape " + n + " is not on the model's root bone at the origin");
                }
                string f = Path.Combine(tmp, srcName + ".bfmdl");
                src.Export(f, res);
                var m = new Model(); m.Import(f, res); m.Name = dst;
                var keep = m.Shapes.Keys.Where(k => names.Contains(k)).Select(k => m.Shapes[k]).ToList();
                var sk = m.Skeleton;
                var root = sk.Bones.Values.First(b => b.ParentIndex < 0);
                string rootName = "r" + dst.Substring(1);
                root.Name = rootName;
                var piv = it["pivot"];
                var P = new Vector3F((float)piv[0], (float)piv[1], (float)piv[2]);
                Bone Make(string name, short parent, Vector3F pos) => new Bone
                {
                    Name = name, ParentIndex = parent, SmoothMatrixIndex = -1, RigidMatrixIndex = -1,
                    BillboardIndex = root.BillboardIndex, Flags = root.Flags, FlagsRotation = root.FlagsRotation,
                    FlagsBillboard = root.FlagsBillboard, FlagsTransform = 0, FlagsTransformCumulative = 0,
                    Scale = new Vector3F(1, 1, 1), Rotation = new Vector4F(0, 0, 0, 1), Position = pos,
                };
                root.SmoothMatrixIndex = -1; root.RigidMatrixIndex = -1;
                sk.Bones.Clear();
                sk.Bones.Add(rootName, root);
                sk.Bones.Add("kdx_pivot", Make("kdx_pivot", 0, P));
                sk.Bones.Add("kdx_appear", Make("kdx_appear", 1, new Vector3F(0, 0, 0)));
                sk.Bones.Add("kdx_inner", Make("kdx_inner", 2, new Vector3F(-P.X, -P.Y, -P.Z)));
                m.Shapes.Clear(); m.VertexBuffers.Clear();
                foreach (var sh in keep)
                {
                    sh.BoneIndex = 3;
                    sh.VertexBufferIndex = (ushort)m.VertexBuffers.Count;
                    m.VertexBuffers.Add(sh.VertexBuffer);
                    m.Shapes.Add(sh.Name, sh);
                }
                res.Models.Add(dst, m);
                foreach (var n in names) src.Shapes.RemoveKey(n);

                int frames = (int)it["frames"];
                var keys = (JObject)it["keys"];
                float First(string kind, int ax, float dflt)
                {
                    var rows = keys[kind] as JArray;
                    return rows != null && rows.Count > 0 ? (float)rows[0][1 + ax] : dflt;
                }
                var a = new SkeletalAnim { Name = dst, FrameCount = frames, FlagsScale = flagsScale,
                                           FlagsRotate = SkeletalAnimFlagsRotate.EulerXYZ, BoneAnims = new List<BoneAnim>() };
                a.Loop = false;
                var rests = new[] { (rootName, new Vector3F(0, 0, 0)), ("kdx_pivot", P), ("kdx_appear", new Vector3F(0, 0, 0)),
                                    ("kdx_inner", new Vector3F(-P.X, -P.Y, -P.Z)) };
                foreach (var (bname, pos) in rests)
                {
                    var ba = new BoneAnim { Name = bname, BeginRotate = 3, BeginTranslate = 6, BeginBaseTranslate = 7 };
                    ba.FlagsBase = BoneAnimFlagsBase.Scale | BoneAnimFlagsBase.Rotate | BoneAnimFlagsBase.Translate;
                    ba.BaseData = new BoneAnimData { Scale = new Vector3F(1, 1, 1), Rotate = new Vector4F(0, 0, 0, 1), Translate = pos };
                    if (bname == "kdx_appear")
                    {
                        var interp = keys["interp"] as JObject;
                        foreach (var (kind, flagX, offX, unit) in new[] {
                                     ("scale", (uint)BoneAnimFlagsCurve.ScaleX, 0x04u, 1f),
                                     ("rotate", (uint)BoneAnimFlagsCurve.RotateX, 0x20u, (float)(Math.PI / 180.0)),
                                     ("translate", (uint)BoneAnimFlagsCurve.TranslateX, 0x10u, 1f) })
                        {
                            var rows = keys[kind] as JArray;
                            if (rows == null || rows.Count < 2) continue;
                            bool smooth = interp != null && (string)interp[kind] == "smooth";
                            var fr = rows.Select(r => (float)r[0]).ToList();
                            for (int ax = 0; ax < 3; ax++)
                            {
                                var vals = rows.Select(r => (float)r[1 + ax] * unit).ToList();
                                if (vals.All(v => Math.Abs(v - vals[0]) < 1e-6f)) continue;
                                ba.Curves.Add(KeyCurve(offX + 4u * (uint)ax, fr, vals, smooth));
                                ba.FlagsCurve |= (BoneAnimFlagsCurve)(flagX << ax);
                            }
                        }
                        // the base pose is frame 0, so the object stays hidden until the path opens
                        ba.BaseData = new BoneAnimData {
                            Scale = new Vector3F(First("scale", 0, 1), First("scale", 1, 1), First("scale", 2, 1)),
                            Rotate = new Vector4F(First("rotate", 0, 0) * (float)(Math.PI / 180.0),
                                                  First("rotate", 1, 0) * (float)(Math.PI / 180.0),
                                                  First("rotate", 2, 0) * (float)(Math.PI / 180.0), 1),
                            Translate = new Vector3F(First("translate", 0, 0), First("translate", 1, 0), First("translate", 2, 0)) };
                    }
                    ba.CalculateTransformFlags();
                    a.BoneAnims.Add(ba);
                }
                a.BindIndices = Enumerable.Range(0, a.BoneAnims.Count).Select(i => (ushort)i).ToArray();
                res.SkeletalAnims.Add(dst, a);
            }
            res.Save((string)spec["output"]);
        }
        finally { try { Directory.Delete(tmp, true); } catch { } }
    }

    // ---- fmaadump: every field of the material animations (to copy their layout) ---------------------------------------
    static JToken Plain(object o)
    {
        var j = new JObject();
        foreach (var pr in o.GetType().GetProperties())
        {
            if (pr.GetIndexParameters().Length > 0) continue;
            object v; try { v = pr.GetValue(o); } catch { continue; }
            if (v == null) continue;
            var t = v.GetType();
            if (t.IsEnum || v is string) j[pr.Name] = v.ToString();
            else if (t.IsPrimitive) j[pr.Name] = new JValue(Convert.ToDouble(v));
            else if (v is ushort[] ua) j[pr.Name] = new JArray(ua.Select(x => (int)x));
            else if (v is float[] fa) j[pr.Name] = new JArray(fa.Take(16));
        }
        return j;
    }

    static JObject FmaaDump(ResFile res)
    {
        var o = new JObject();
        var dicts = new (string, ResDict<MaterialAnim>)[] { ("ShaderParamAnims", res.ShaderParamAnims), ("TexSrtAnims", res.TexSrtAnims),
            ("ColorAnims", res.ColorAnims), ("TexPatternAnims", res.TexPatternAnims), ("MatVisibilityAnims", res.MatVisibilityAnims) };
        foreach (var (dn, dict) in dicts)
            foreach (var a in dict.Values)
            {
                var ja = (JObject)Plain(a);
                ja["dict"] = dn;
                ja["TextureNames"] = a.TextureNames?.Count ?? 0;
                var mats = new JArray();
                foreach (var d in a.MaterialAnimDataList)
                {
                    var jd = (JObject)Plain(d);
                    jd["params"] = new JArray((d.ParamAnimInfos ?? new List<ParamAnimInfo>()).Select(x => Plain(x)));
                    jd["curves"] = new JArray((d.Curves ?? new List<AnimCurve>()).Select(c => { var jc = (JObject)Plain(c); jc["n"] = c.Frames?.Length ?? 0;
                        jc["keys0"] = c.Keys == null ? null : new JArray(Enumerable.Range(0, Math.Min(4, c.Keys.GetLength(0))).Select(i => c.Keys[i, 0])); return jc; }));
                    jd["constants"] = new JArray((d.Constants ?? new List<AnimConstant>()).Select(x => Plain(x)));
                    mats.Add(jd);
                }
                ja["materials"] = mats;
                o[a.Name] = ja;
            }
        return o;
    }

    // Moving images on a World Maker map (phase 5). Every world builds a decoration model "CS_W<n>_Synchro" and plays the
    // skeletal animation of the same name in a loop (MapModel::createDecorateAnimModel 0x60226F74 -> "%s_%s" with
    // "Synchro" for every world; vanilla CS_W1_Synchro sways palm leaves, 400 frames, looping). This moves shapes of the
    // source model into that model, each item on its own bones root <- kdx_sNN_pivot (at the pivot) <- kdx_sNN (animated)
    // <- kdx_sNN_in (back at the origin, holding the shapes), and writes the looping animation:
    // {"input", "output", "source_model", "model": "CS_W1_Synchro", "frames": 600, "flags_from": "<bfres>"?,
    //  "items": [{"shapes": [...], "pivot": [x, y, z], "curves": [{"channel": "sx|sy|sz|rx|ry|rz|tx|ty|tz",
    //            "frames": [...], "values": [...] (rotate in degrees), "mode": "linear|smooth|step|hermite", "slopes": [...]}]}]}
    static void Synchro(JObject spec)
    {
        var res = new ResFile((string)spec["input"]);
        string dst = (string)spec["model"], srcName = (string)spec["source_model"];
        var flagsScale = res.SkeletalAnims.Values.Select(x => x.FlagsScale).FirstOrDefault();
        if (res.SkeletalAnims.Count == 0 && spec["flags_from"] != null)
            flagsScale = new ResFile((string)spec["flags_from"]).SkeletalAnims.Values.Select(x => x.FlagsScale).FirstOrDefault();
        if (res.SkeletalAnims.Count == 0 && spec["flags_from"] == null) flagsScale = SkeletalAnimFlagsScale.Maya;
        if (res.Models.ContainsKey(dst)) res.Models.RemoveKey(dst);
        if (res.SkeletalAnims.ContainsKey(dst)) res.SkeletalAnims.RemoveKey(dst);
        var src = res.Models[srcName];
        var items = ((JArray)spec["items"]).Cast<JObject>().ToList();
        foreach (var it in items)
            foreach (var n in it["shapes"].Select(x => (string)x))
            {
                if (!src.Shapes.ContainsKey(n)) throw new Exception("no shape " + n + " in " + srcName);
                var sh = src.Shapes[n];
                if (sh.VertexSkinCount != 0) throw new Exception("shape " + n + " is skinned");
                var bn = src.Skeleton.Bones[sh.BoneIndex];
                if (bn.ParentIndex >= 0 || Math.Abs(bn.Position.X) + Math.Abs(bn.Position.Y) + Math.Abs(bn.Position.Z) > 1e-4f)
                    throw new Exception("shape " + n + " is not on the model's root bone at the origin");
            }
        string f = Tmp() + "_synchro.bfmdl";
        src.Export(f, res);
        var m = new Model(); m.Import(f, res); m.Name = dst;
        var sk = m.Skeleton;
        var root = sk.Bones.Values.First(b => b.ParentIndex < 0);
        root.Name = dst; root.SmoothMatrixIndex = -1; root.RigidMatrixIndex = -1;
        Bone Make(string name, short parent, Vector3F pos) => new Bone
        {
            Name = name, ParentIndex = parent, SmoothMatrixIndex = -1, RigidMatrixIndex = -1,
            BillboardIndex = root.BillboardIndex, Flags = root.Flags, FlagsRotation = root.FlagsRotation,
            FlagsBillboard = root.FlagsBillboard, FlagsTransform = 0, FlagsTransformCumulative = 0,
            Scale = new Vector3F(1, 1, 1), Rotation = new Vector4F(0, 0, 0, 1), Position = pos,
        };
        var oldShapes = m.Shapes.Values.ToDictionary(s => s.Name);
        sk.Bones.Clear(); m.Shapes.Clear(); m.VertexBuffers.Clear();
        sk.Bones.Add(dst, root);
        int frames = (int)spec["frames"];
        var a = new SkeletalAnim { Name = dst, FrameCount = frames, FlagsScale = flagsScale,
                                   FlagsRotate = SkeletalAnimFlagsRotate.EulerXYZ, BoneAnims = new List<BoneAnim>() };
        a.Loop = true;
        BoneAnim Rest(string name, Vector3F pos)
        {
            var ba = new BoneAnim { Name = name, BeginRotate = 3, BeginTranslate = 6, BeginBaseTranslate = 7 };
            ba.FlagsBase = BoneAnimFlagsBase.Scale | BoneAnimFlagsBase.Rotate | BoneAnimFlagsBase.Translate;
            ba.BaseData = new BoneAnimData { Scale = new Vector3F(1, 1, 1), Rotate = new Vector4F(0, 0, 0, 1), Translate = pos };
            return ba;
        }
        var rootAnim = Rest(dst, new Vector3F(0, 0, 0)); rootAnim.CalculateTransformFlags(); a.BoneAnims.Add(rootAnim);
        const string Channels = "sxsyszrxryrztxtytz";
        for (int i = 0; i < items.Count; i++)
        {
            var it = items[i];
            var piv = it["pivot"];
            var P = new Vector3F((float)piv[0], (float)piv[1], (float)piv[2]);
            string bp = string.Format("kdx_s{0:D2}", i);
            short pi = (short)sk.Bones.Count;
            sk.Bones.Add(bp + "_pivot", Make(bp + "_pivot", 0, P));
            sk.Bones.Add(bp, Make(bp, pi, new Vector3F(0, 0, 0)));
            sk.Bones.Add(bp + "_in", Make(bp + "_in", (short)(pi + 1), new Vector3F(-P.X, -P.Y, -P.Z)));
            foreach (var n in it["shapes"].Select(x => (string)x))
            {
                var sh = oldShapes[n];
                sh.BoneIndex = (ushort)(pi + 2);
                sh.VertexBufferIndex = (ushort)m.VertexBuffers.Count;
                m.VertexBuffers.Add(sh.VertexBuffer);
                m.Shapes.Add(sh.Name, sh);
                src.Shapes.RemoveKey(n);
            }
            var pa = Rest(bp + "_pivot", P); pa.CalculateTransformFlags();
            var ba = Rest(bp, new Vector3F(0, 0, 0));
            float[] b = { 1, 1, 1, 0, 0, 0, 0, 0, 0 };
            foreach (JObject c in (JArray)it["curves"] ?? new JArray())
            {
                int ch = Channels.IndexOf((string)c["channel"]) / 2;
                if (ch < 0) throw new Exception("unknown channel " + (string)c["channel"]);
                int kind = ch / 3, ax = ch % 3;
                float unit = kind == 1 ? (float)(Math.PI / 180.0) : 1f;
                var fr = c["frames"].Select(x => (float)x).ToList();
                var vals = c["values"].Select(x => (float)x * unit).ToList();
                b[ch] = vals[0];
                if (vals.All(v => Math.Abs(v - vals[0]) < 1e-6f)) continue;
                var slopes = c["slopes"]?.Select(x => (float)x * unit).ToList();
                uint off = (kind == 0 ? 0x04u : kind == 1 ? 0x20u : 0x10u) + 4u * (uint)ax;
                uint flag = kind == 0 ? (uint)BoneAnimFlagsCurve.ScaleX : kind == 1 ? (uint)BoneAnimFlagsCurve.RotateX
                                                                         : (uint)BoneAnimFlagsCurve.TranslateX;
                ba.Curves.Add(ModeCurve(off, fr, vals, (string)c["mode"] ?? "linear", slopes));
                ba.FlagsCurve |= (BoneAnimFlagsCurve)(flag << ax);
            }
            // curves in the order the game reads them: scale, rotate, translate (by target offset)
            ba.Curves = ba.Curves.OrderBy(c => c.AnimDataOffset == 0x20 || c.AnimDataOffset == 0x24 || c.AnimDataOffset == 0x28 ? 1
                                               : c.AnimDataOffset >= 0x10 && c.AnimDataOffset < 0x1C ? 2 : 0)
                                 .ThenBy(c => c.AnimDataOffset).ToList();
            ba.BaseData = new BoneAnimData { Scale = new Vector3F(b[0], b[1], b[2]), Rotate = new Vector4F(b[3], b[4], b[5], 1),
                                             Translate = new Vector3F(b[6], b[7], b[8]) };
            ba.CalculateTransformFlags();
            var ia = Rest(bp + "_in", new Vector3F(-P.X, -P.Y, -P.Z)); ia.CalculateTransformFlags();
            a.BoneAnims.Add(pa); a.BoneAnims.Add(ba); a.BoneAnims.Add(ia);
        }
        a.BindIndices = Enumerable.Range(0, a.BoneAnims.Count).Select(i => (ushort)i).ToArray();
        // only the materials its shapes use (a copied route material would be one more for the route animations)
        var used = m.Shapes.Values.Select(sh => (int)sh.MaterialIndex).Distinct().OrderBy(x => x).ToList();
        var mats = new ResDict<Material>();
        foreach (int mi in used) mats.Add(m.Materials[mi].Name, m.Materials[mi]);
        foreach (var sh in m.Shapes.Values) sh.MaterialIndex = (ushort)used.IndexOf(sh.MaterialIndex);
        m.Materials = mats;
        res.Models.Add(dst, m);
        res.SkeletalAnims.Add(dst, a);
        res.Save((string)spec["output"]);
    }

    // KeyCurve with more modes: "step" holds each key until the next, "hermite" takes slopes (value per frame) at each key.
    static AnimCurve ModeCurve(uint target, List<float> frames, List<float> values, string mode, List<float> slopes)
    {
        if (mode == "linear" || mode == "smooth") return KeyCurve(target, frames, values, mode == "smooth");
        var c = KeyCurve(target, frames, values, false);
        for (int i = 0; i < frames.Count; i++)
        {
            float d = i + 1 < frames.Count ? values[i + 1] - values[i] : 0f;
            float L = i + 1 < frames.Count ? frames[i + 1] - frames[i] : 0f;
            float m0 = mode == "hermite" && slopes != null ? slopes[i] * L : 0f;
            float m1 = mode == "hermite" && slopes != null && i + 1 < frames.Count ? slopes[i + 1] * L : 0f;
            c.Keys[i, 0] = values[i];
            c.Keys[i, 1] = mode == "step" ? 0f : m0;
            c.Keys[i, 2] = mode == "step" ? 0f : 3f * d - 2f * m0 - m1;
            c.Keys[i, 3] = mode == "step" ? 0f : -2f * d + m0 + m1;
        }
        return c;
    }

}

"""
kdx/assetkit.py - scenery assets cut out of game worlds (NSMBUDX CS_W*.szs, NSMBW CS_W*.arc) as COLLADA models.

    parts = native_parts(world, materials={"mtParmLeef", "mtParmTrunk"})    # NSMBUDX shapes, world space
    parts = wii_parts(arc_path, {"mtYamaBlue", "mtYamaBlueTop"}, out_dir)  # NSMBW materials, world space
    for obj in pieces(parts):                                             # one ObjModel per connected object
        model, base = centred(obj)                                        # its foot at the origin
        save(model, "hill.dae")

A world draws many objects with one shape or material (every hill of a colour, every palm's leaves). pieces() splits
them again: triangles sharing a corner position are one piece, and pieces standing on or inside each other (a hill's
cap on its body, a palm's leaves round its trunk) are one object.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import model3d
from .objmodel import ObjModel, Part


def native_parts(world, models=None, materials=None, shapes=None):
    """Parts (world space, bind pose) of a NSMBUDX world's shapes, filtered by model, material or shape names."""
    from . import worldedit
    out = []
    for mname, sname, mat, part in worldedit.world_shapes(world, set(models) if models else None):
        if materials is not None and mat not in materials:
            continue
        if shapes is not None and sname not in shapes:
            continue
        q = part.copy()
        q.material = mat or sname
        out.append(q)
    return out


def wii_parts(arc, materials, out_dir, shade_levels=5, pose=None):
    """Parts of a NSMBW model drawn by the given materials (textures decoded into out_dir)."""
    res, mdl = model3d.brres_model(arc)
    skip = tuple(m.name for m in mdl.materials if m.name not in materials)
    m = model3d.load_brres(arc, skip_materials=skip, out_dir=Path(out_dir), shade_levels=shade_levels, pose=pose)
    return list(m.parts)


def _subset(part, tris):
    corners = (np.asarray(tris)[:, None] * 3 + np.arange(3)).ravel()
    q = Part(part.material, part.positions[corners], part.normals[corners], part.uvs[corners], part.texture,
             part.color, part.mode, part.double_sided, None if part.colors is None else part.colors[corners])
    q.wrap = part.wrap
    if part.uvs2 is not None:
        q.uvs2, q.mask_offset = part.uvs2[corners], part.mask_offset
    return q


def pieces(parts, merge_inside=True, weld=0.05):
    """One ObjModel per connected object, biggest first."""
    pos = np.concatenate([p.positions for p in parts]).astype(np.float64)
    key = np.round(pos / weld).astype(np.int64)
    _u, corner = np.unique(key, axis=0, return_inverse=True)
    corner = corner.reshape(-1, 3)
    parent = np.arange(corner.max() + 1)

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b, c in corner.tolist():
        ra, rb, rc = root(a), root(b), root(c)
        parent[rb] = ra
        parent[root(rc)] = ra
    tri_root = np.array([root(a) for a in corner[:, 0].tolist()])
    groups = {}
    for i, r in enumerate(tri_root.tolist()):
        groups.setdefault(r, []).append(i)
    tri_pos = pos.reshape(-1, 3, 3)
    boxes = {r: (tri_pos[t].reshape(-1, 3).min(0), tri_pos[t].reshape(-1, 3).max(0)) for r, t in groups.items()}
    order = sorted(groups, key=lambda r: -np.prod(np.maximum(boxes[r][1] - boxes[r][0], 1e-3)[[0, 2]]))
    owner = {}
    if merge_inside:                       # a piece whose footprint lies in a bigger one belongs to it
        for i, r in enumerate(order):
            lo, hi = boxes[r]
            for big in order[:i]:
                if big in owner:
                    continue
                blo, bhi = boxes[big]
                pad = 0.15 * np.maximum(bhi - blo, 1.0)
                if np.all(lo[[0, 2]] >= blo[[0, 2]] - pad[[0, 2]]) and np.all(hi[[0, 2]] <= bhi[[0, 2]] + pad[[0, 2]]) \
                        and lo[1] <= bhi[1] + pad[1] and hi[1] >= blo[1] - pad[1]:
                    owner[r] = big
                    break
    objects = {}
    for r in order:
        top = r
        while top in owner:
            top = owner[top]
        objects.setdefault(top, []).extend(groups[r])
    starts = np.cumsum([0] + [p.triangle_count for p in parts])
    out = []
    for top in order:
        if top not in objects:
            continue
        tris = np.array(sorted(objects[top]))
        sub = []
        for k, p in enumerate(parts):
            sel = tris[(tris >= starts[k]) & (tris < starts[k + 1])] - starts[k]
            if len(sel):
                sub.append(_subset(p, sel))
        out.append(ObjModel(sub))
    return out


def clusters(objs, gap=0.0):
    """Objects whose boxes (grown by gap) touch, as one ObjModel each, biggest footprint first: a plant drawn as two
    halves (Soda Jungle's fan plant), a bush made of separate leaves and fruit (NSMBW's fruit bush)."""
    if not objs:
        return []
    b = np.array([np.r_[o.bounds()[0], o.bounds()[1]] for o in objs], np.float64)
    lo, hi = b[:, :3] - gap, b[:, 3:] + gap
    touch = np.all(lo[:, None] <= hi[None], axis=2) & np.all(lo[None] <= hi[:, None], axis=2)
    parent = list(range(len(objs)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in zip(*np.nonzero(np.triu(touch, 1))):
        parent[root(int(j))] = root(int(i))
    groups = {}
    for i in range(len(objs)):
        groups.setdefault(root(i), []).append(i)
    out = [ObjModel([p for i in g for p in objs[i].parts]) for g in groups.values()]

    def area(m):
        lo_, hi_ = m.bounds()
        return (hi_[0] - lo_[0]) * (hi_[2] - lo_[2])
    return sorted(out, key=lambda m: -area(m))


def centred(model, ground=None):
    """The model moved so its footprint's centre is at x = z = 0 and its lowest point (or `ground`) at y = 0.
    Returns (model, where it was: the offset to add back)."""
    lo, hi = model.bounds()
    base = np.array([(lo[0] + hi[0]) / 2, lo[1] if ground is None else ground, (lo[2] + hi[2]) / 2])
    m = np.identity(4)
    m[:3, 3] = -base
    return model.transformed(m), base


def save(model, path):
    """COLLADA with the textures next to it (PNG)."""
    return model3d.save_dae(model, Path(path))

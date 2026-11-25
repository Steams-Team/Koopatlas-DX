"""
kdx/roadtrace.py - follow the road drawn in a map picture (World Maker Route tool, "Follow the road").

Between two clicks the route takes the cheapest way over the picture, cheap where the colour is like the road's (sampled
under the clicks: you click on the road), dear elsewhere; the way is then thinned to the few bends the game needs
(Mario walks straight from bend to bend).

    bends = trace(image, (w, h), (x0, y0), (x1, y1))   # tiles; [] when there is no road to follow
"""
from __future__ import annotations

import heapq
import math

import numpy as np

RES = 4                 # cells per tile
_cache = {}


def _grid(image, size):
    """The picture as float RGB + alpha arrays at RES cells per tile (cached per image and map size)."""
    from PyQt5 import QtCore, QtGui
    key = (image.cacheKey(), size)
    if key in _cache:
        return _cache[key]
    w, h = size[0] * RES, size[1] * RES
    img = image.scaled(w, h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.byteCount())
    a = np.frombuffer(bytes(ptr), np.uint8).reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).astype(np.float32)
    _cache.clear()
    _cache[key] = a
    return a


def _sample(g, cx, cy, r=1):
    h, w = g.shape[:2]
    y0, y1, x0, x1 = max(0, cy - r), min(h, cy + r + 1), max(0, cx - r), min(w, cx + r + 1)
    return np.median(g[y0:y1, x0:x1, :3].reshape(-1, 3), axis=0)


def _simplify(pts, tol):
    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    """Douglas-Peucker on a list of (x, y)."""
    if len(pts) < 3:
        return list(pts)
    a, b = np.asarray(pts[0]), np.asarray(pts[-1])
    ab = b - a
    L = float(np.hypot(*ab)) or 1e-9
    d = [abs(float(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0]))) / L for p in pts[1:-1]]
    i = int(np.argmax(d)) + 1
    if d[i - 1] <= tol:
        return [pts[0], pts[-1]]
    return _simplify(pts[:i + 1], tol)[:-1] + _simplify(pts[i:], tol)


def trace(image, size, start, end, tolerance=0.35, min_gap=0.9, strict=1.0, colours=()):
    """Bends (tiles, without the two ends) of the road between start and end in the picture, or [] when the straight
    line is as good (no road, or a straight road). colours: more road colours (RGB) to follow, such as the ones under
    the earlier clicks of the same path."""
    if image is None or image.isNull():
        return []
    g = _grid(image, size)
    H, W = g.shape[:2]
    s = (min(W - 1, max(0, int(start[0] * RES))), min(H - 1, max(0, int(start[1] * RES))))
    t = (min(W - 1, max(0, int(end[0] * RES))), min(H - 1, max(0, int(end[1] * RES))))
    if s == t:
        return []
    cols = [_sample(g, *s), _sample(g, *t)] + [np.asarray(c, np.float32) for c in colours]
    span = math.hypot(t[0] - s[0], t[1] - s[1])
    m = int(max(6 * RES, span * 0.6))
    x0, x1 = max(0, min(s[0], t[0]) - m), min(W, max(s[0], t[0]) + m + 1)
    y0, y1 = max(0, min(s[1], t[1]) - m), min(H, max(s[1], t[1]) + m + 1)
    sub = g[y0:y1, x0:x1]
    dist = np.min([np.linalg.norm(sub[..., :3] - c, axis=-1) for c in cols], axis=0)
    cost = 1.0 + 60.0 * strict * np.clip(dist / 110.0, 0, 1) ** 2
    cost[sub[..., 3] < 64] = 80.0                           # clear (transparent) parts of the picture: no road
    sw, sh = x1 - x0, y1 - y0
    S, T = (s[0] - x0, s[1] - y0), (t[0] - x0, t[1] - y0)
    best = np.full((sh, sw), np.inf, np.float32)
    prev = np.full((sh, sw), -1, np.int64)
    best[S[1], S[0]] = 0.0
    heap = [(0.0, 0.0, S)]
    R2 = math.sqrt(2.0)
    steps = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, R2), (1, -1, R2), (-1, 1, R2), (-1, -1, R2)]
    hscale = 1.0                                            # the cheapest cell costs 1: admissible
    while heap:
        _f, gc, (x, y) = heapq.heappop(heap)
        if (x, y) == T:
            break
        if gc > best[y, x]:
            continue
        for dx, dy, L in steps:
            nx, ny = x + dx, y + dy
            if 0 <= nx < sw and 0 <= ny < sh:
                ng = gc + L * 0.5 * (cost[y, x] + cost[ny, nx])
                if ng < best[ny, nx]:
                    best[ny, nx] = ng
                    prev[ny, nx] = y * sw + x
                    hx, hy = abs(T[0] - nx), abs(T[1] - ny)
                    heapq.heappush(heap, (ng + hscale * (max(hx, hy) + (R2 - 1) * min(hx, hy)), ng, (nx, ny)))
    if not np.isfinite(best[T[1], T[0]]):
        return []
    path, k = [], T[1] * sw + T[0]
    while k >= 0:
        y, x = divmod(int(k), sw)
        path.append(((x + x0 + 0.5) / RES, (y + y0 + 0.5) / RES))
        k = prev[y, x]
    path.reverse()
    # no road to follow: the way costs about as much as the straight line over unlike colours
    straight = np.linspace(0, 1, max(2, int(span)))
    sc = sum(cost[int(S[1] + (T[1] - S[1]) * f), int(S[0] + (T[0] - S[0]) * f)] for f in straight) / len(straight)
    if sc < 2.0:                                            # the straight line already runs on the road
        return []
    walked = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))
    if walked > 2.2 * math.hypot(end[0] - start[0], end[1] - start[1]) + 2.0:
        return []                                           # a long way round: not the road the clicks meant
    path[0], path[-1] = tuple(start), tuple(end)
    pts = _simplify(path, tolerance)
    out = []
    for p in pts[1:-1]:
        last = out[-1] if out else start
        if math.hypot(p[0] - last[0], p[1] - last[1]) >= min_gap and math.hypot(p[0] - end[0], p[1] - end[1]) >= min_gap:
            out.append((round(p[0], 2), round(p[1], 2)))
    return out


def colour_at(image, size, xy):
    """The picture's colour (RGB) under a point given in tiles."""
    g = _grid(image, size)
    H, W = g.shape[:2]
    return tuple(float(v) for v in _sample(g, min(W - 1, max(0, int(xy[0] * RES))), min(H - 1, max(0, int(xy[1] * RES)))))

"""Pure-stdlib geometry: decode Natural Earth TopoJSON and classify flight tracks.

A flight "violates" the airspace rule if any track point (after densifying long
gaps) falls inside an excluded country or inside an excluded sea corridor
(Persian Gulf, Gulf of Oman, Red Sea, Gulf of Aden; land borders alone miss
those). Passing within `buffer_km` of excluded land is reported as a warning.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "countries-50m.json"
EARTH_KM = 6371.0

# Rough water corridors that sit inside Middle East FIRs. (lon, lat) rings.
SEA_ZONES: dict[str, list[tuple[float, float]]] = {
    "Persian Gulf": [(47.9, 30.6), (50.5, 30.3), (56.6, 27.3), (56.9, 26.0),
                     (56.4, 24.0), (51.5, 23.7), (50.0, 25.3), (48.0, 28.6)],
    "Gulf of Oman": [(56.4, 26.6), (61.8, 25.3), (61.8, 22.3), (59.8, 22.3),
                     (56.4, 23.9)],
    "Red Sea": [(32.3, 30.1), (35.1, 29.6), (39.2, 22.0), (43.6, 12.7),
                (43.2, 12.3), (41.0, 14.4), (36.6, 22.0), (33.4, 27.6)],
    "Gulf of Aden": [(43.3, 12.8), (51.4, 12.2), (51.4, 10.9), (43.2, 11.3)],
}


def _decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def _ring(arcs, idxs):
    out: list[tuple[float, float]] = []
    for i in idxs:
        pts = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
        out.extend(pts if not out else pts[1:])
    return out


class Polygon:
    """Polygon with holes; point test via ray casting, bbox prefilter."""

    def __init__(self, rings: list[list[tuple[float, float]]]):
        self.rings = [r for r in rings if len(r) >= 3]
        xs = [p[0] for p in self.rings[0]]
        ys = [p[1] for p in self.rings[0]]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def contains(self, lon: float, lat: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        inside = False
        for ring in self.rings:  # holes flip parity
            if _in_ring(ring, lon, lat):
                inside = not inside
        return inside

    def edges(self):
        for ring in self.rings:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                yield a, b


def _in_ring(ring, x, y) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


@lru_cache(maxsize=1)
def countries() -> dict[str, list[Polygon]]:
    topo = json.loads(DATA.read_text())
    arcs = _decode_arcs(topo)
    out: dict[str, list[Polygon]] = {}
    for g in topo["objects"]["countries"]["geometries"]:
        name = g.get("properties", {}).get("name")
        if not name or g.get("type") is None:
            continue
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        out[name] = [Polygon([_ring(arcs, r) for r in poly]) for poly in polys]
    return out


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def densify(points: list[tuple[float, float]], max_gap_km: float = 50.0):
    """Insert great-circle-ish (linear in lon/lat; fine at these gaps) points."""
    if not points:
        return []
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        d = haversine_km(a, b)
        if d > max_gap_km:
            n = int(d // max_gap_km)
            dl = b[0] - a[0]
            if abs(dl) > 180:  # antimeridian
                dl -= math.copysign(360, dl)
            for k in range(1, n + 1):
                t = k / (n + 1)
                lon = a[0] + dl * t
                lon = (lon + 540) % 360 - 180
                out.append((lon, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    return out


def _seg_dist_km(p, a, b) -> float:
    """Approx distance from p to segment ab using a local equirectangular plane."""
    lat0 = math.radians(p[1])
    kx = 111.32 * math.cos(lat0)
    ky = 110.57
    px, py = 0.0, 0.0
    ax, ay = (a[0] - p[0]) * kx, (a[1] - p[1]) * ky
    bx, by = (b[0] - p[0]) * kx, (b[1] - p[1]) * ky
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(cx - px, cy - py)


def near_country(lon, lat, polys: list[Polygon], buffer_km: float) -> bool:
    deg = buffer_km / 80.0  # generous degree margin for bbox prefilter
    for poly in polys:
        x0, y0, x1, y1 = poly.bbox
        if not (x0 - deg <= lon <= x1 + deg and y0 - deg <= lat <= y1 + deg):
            continue
        for a, b in poly.edges():
            if (min(a[0], b[0]) - deg <= lon <= max(a[0], b[0]) + deg and
                    min(a[1], b[1]) - deg <= lat <= max(a[1], b[1]) + deg):
                if _seg_dist_km((lon, lat), a, b) <= buffer_km:
                    return True
    return False


def classify_track(points: list[tuple[float, float]], excluded: list[str],
                   buffer_km: float = 25.0, sea_zones: bool = True) -> dict:
    """points: [(lon, lat), ...]. Returns countries crossed + violations."""
    cs = countries()
    missing = [n for n in excluded if n not in cs]
    if missing:
        raise ValueError(f"Unknown country names in exclusion list: {missing}")
    excl = {n: cs[n] for n in excluded}
    seas = {k: Polygon([v]) for k, v in SEA_ZONES.items()} if sea_zones else {}
    crossed: list[str] = []
    hits: dict[str, list[tuple[float, float]]] = {}
    near: dict[str, list[tuple[float, float]]] = {}
    pts = densify(points)
    for lon, lat in pts:
        here = None
        for name, polys in cs.items():
            if any(p.contains(lon, lat) for p in polys):
                here = name
                break
        if here and (not crossed or crossed[-1] != here):
            crossed.append(here)
        if here in excl:
            hits.setdefault(here, []).append((lon, lat))
            continue
        for z, poly in seas.items():
            if poly.contains(lon, lat):
                hits.setdefault(z, []).append((lon, lat))
                break
        else:
            if buffer_km > 0:
                for name, polys in excl.items():
                    if near_country(lon, lat, polys, buffer_km):
                        near.setdefault(f"within {buffer_km:.0f} km of {name}", []).append((lon, lat))
                        break
    return {
        "countries_crossed": crossed,
        "violations": {k: {"points": len(v), "example": [round(v[0][1], 2), round(v[0][0], 2)]}
                       for k, v in hits.items()},
        "warnings": {k: {"points": len(v), "example": [round(v[0][1], 2), round(v[0][0], 2)]}
                     for k, v in near.items()},
        "compliant": not hits,
        "n_points": len(points),
    }

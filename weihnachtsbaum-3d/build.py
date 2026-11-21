#!/usr/bin/env python
"""
Klemmbaustein-kompatibler Weihnachtsbaum (3D-Druck, modular)

Alles ist parametrisch. Start:  python build.py
Erzeugt STLs in ./stl, prueft Kollisionen der Montage und rendert Vorschaubilder.

Koordinaten: mm, z nach oben. Jedes Teil ist in Druckorientierung gebaut
(Unterseite auf dem Bett, Noppen oben) -> keine Stuetzen noetig.
"""
import os, math, random, csv
import numpy as np
from manifold3d import Manifold, CrossSection, FillRule, OpType
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "stl")

# ---------------- Klemmbaustein-Normmasse (+ Druckkorrektur) ----------------
P = 8.0            # Rastermass
PLATE = 3.2        # Plattenhoehe
BRICK = 9.6        # Steinhoehe
STUD_D = 4.98      # gemessen: Modell 4.78 -> gedruckt 4.57 (-0.21); Original 4.87 -> Ziel gedruckt ~4.85
STUD_H = 1.7
TUBE_OD = 6.42     # Original 6.5 aussen; Modell 6.65 wurde 6.73 gedruckt (+0.08) -> Wand war zu dick (0.93 statt 0.85)
TUBE_CH = 0.3      # Fase am unteren Roehrenrand (Elefantenfuss am Druckbett: dort wurden 6.73 statt ~6.5 gemessen)
TUBE_ID = 5.05     # Original 4.8 innen; Loch schrumpft ca. 0.2 -> gedruckt ~4.85 (Noppe passt rein)
WALL = 1.2
CEIL = 1.2         # Deckendicke auf der Unterseite
GAP = 0.1          # Spiel zwischen Nachbarsteinen (Aussenkontur)
ORN_ID = 4.85      # Innenloch der 1x1-Rundplatten
SEG = 48

# ---------------------------------------------------------------- Helfer ----
def union(ms):
    ms = [m for m in ms if m is not None]
    return ms[0] if len(ms) == 1 else Manifold.batch_boolean(ms, OpType.Add)

def to_cs(g):
    polys = [g] if isinstance(g, Polygon) else [p for p in g.geoms if isinstance(p, Polygon)]
    rings = []
    for p in polys:
        rings.append(np.asarray(p.exterior.coords)[:-1])
        for h in p.interiors:
            rings.append(np.asarray(h.coords)[:-1])
    return CrossSection(rings, FillRule.EvenOdd)

def extr(g, z0, z1):
    return Manifold.extrude(to_cs(g), z1 - z0).translate((0, 0, z0))

def cyl(x, y, z, h, r):
    return Manifold.cylinder(h, r, r, SEG).translate((x, y, z))

def stud(x, y, z, d=None):
    r = (d or STUD_D) / 2
    c = 0.2
    return union([Manifold.cylinder(STUD_H - c, r, r, SEG),
                  Manifold.cylinder(c, r, r - 0.15, SEG).translate((0, 0, STUD_H - c))]
                 ).translate((x, y, z))

def tube(x, y, h, od=None):
    od = od or TUBE_OD
    ch = min(TUBE_CH, h / 2)
    t = union([Manifold.cylinder(ch, od / 2 - ch, od / 2, SEG),
               Manifold.cylinder(h - ch, od / 2, od / 2, SEG).translate((0, 0, ch))]).translate((x, y, 0))
    if h > 1.5:
        t = t - cyl(x, y, -0.1, h + 0.1, TUBE_ID / 2)
    return t

def make_part(outline, h, cavity=None, cavity_h=None, tube_pts=(), studs=(),
              tube_od=None, stud_d=None):
    m = extr(outline, 0, h)
    if cavity is not None:
        ch = cavity_h if cavity_h else h - CEIL
        m = m - extr(cavity, -0.5, ch)
        m = union([m] + [tube(x, y, ch, tube_od) for x, y in tube_pts])
    if studs:
        m = union([m] + [stud(x, y, h, stud_d) for x, y in studs])
    return m

def cell_center(i, j):
    return ((i + .5) * P, (j + .5) * P)

def cells_poly(cells):
    return unary_union([box(i * P, j * P, (i + 1) * P, (j + 1) * P) for i, j in cells])

def lattice_tubes(cells):
    s = set(cells)
    cand = {(i + a, j + b) for i, j in s for a in (0, 1) for b in (0, 1)}
    return [(a * P, b * P) for a, b in sorted(cand)
            if all(c in s for c in ((a - 1, b - 1), (a, b - 1), (a - 1, b), (a, b)))]

def disc_cells(rmax, rng=9):
    return [(i, j) for i in range(-rng, rng) for j in range(-rng, rng)
            if math.hypot(*cell_center(i, j)) <= rmax]

def fit_cells(region, margin=0.6, rng=9):
    out = []
    for i in range(-rng, rng):
        for j in range(-rng, rng):
            if Point(cell_center(i, j)).buffer(STUD_D / 2 + margin).within(region):
                out.append((i, j))
    return out

def brick(i0, j0, w, d, h, tube_od=None, stud_d=None):
    cells = [(i, j) for i in range(i0, i0 + w) for j in range(j0, j0 + d)]
    outline = cells_poly(cells).buffer(-GAP, join_style=2)
    cavity = outline.buffer(-WALL, join_style=2)
    return make_part(outline, h, cavity, None, lattice_tubes(cells),
                     [cell_center(i, j) for i, j in cells], tube_od, stud_d)

def lobed(R, phi, amp=0.05, lobes=8, n=360):
    th = np.linspace(0, 2 * math.pi, n, endpoint=False)
    r = R * (1 + amp * np.cos(lobes * (th - phi)))
    return Polygon(np.c_[r * np.cos(th), r * np.sin(th)])

def disc(R, n=96):
    return Point(0, 0).buffer(R, n // 4)

def ornament(x, y, z, id_=None):
    m = make_part(disc(P / 2 - 0.1, 48), PLATE, disc((id_ or ORN_ID) / 2, 48),
                  cavity_h=2.0, studs=[(0, 0)])
    return m.translate((x, y, z))

# ------------------------------------------------------------ Baum-Daten ----
# (Rb = Radius unterste Platte, rt = Radius oberste Platte, n Platten, Rock-Breite)
TIERS = [
    (52, 24, 6, 16),
    (46, 22, 6, 14),
    (40, 20, 6, 12),
    (34, 18, 5, 11),
    (28, 14, 5, 10),
    (22, 11, 4, 8),
    (16, 10, 3, 4),
]
TRUNK_H = 19.2
BASE_BROWN_H = 2 * PLATE
BASE_WHITE_H = PLATE
BASE_R_BROWN, BASE_R_WHITE = 52, 56
BASE_CELLS_UNDER = disc_cells(43)     # Noppen des braunen Sockels = Klemmflaeche der weissen Platte
BASE_CELLS_TOP = disc_cells(49)       # Noppen der weissen Platte

def tier_radii(Rb, rt, n, skirt):
    r1 = Rb - skirt
    rest = n - 2
    return [Rb, r1] + [r1 + (rt - r1) * k / rest for k in range(1, rest + 1)]

def build_tiers():
    tiers = []
    top_cells = [(-1, -1), (0, -1), (-1, 0), (0, 0)]       # Stamm: 2x2 Noppen
    outlines_all = []
    for ti, (Rb, rt, n, sk) in enumerate(TIERS):
        phi = 0 if ti % 2 == 0 else math.pi / 8
        rad = tier_radii(Rb, rt, n, sk)
        outlines_all.append([lobed(r, phi) for r in rad])
    ornament_slots = []
    z = BASE_BROWN_H + BASE_WHITE_H + TRUNK_H
    for ti, (Rb, rt, n, sk) in enumerate(TIERS):
        O = outlines_all[ti]
        nxt_flange = outlines_all[ti + 1][0] if ti + 1 < len(TIERS) else None
        cavity_cells = top_cells
        new_top = fit_cells(O[-1])
        # Ringnoppen (fuer Schmuck)
        ring = []
        for k in range(n - 1):
            region = O[k]
            blocked = O[k + 1].buffer(P / 2 + 0.2)     # Schmuck-Radius 3.9 + Luft
            for i in range(-8, 8):
                for j in range(-8, 8):
                    c = cell_center(i, j)
                    if not Point(c).buffer(STUD_D / 2 + 0.5).within(region):
                        continue
                    if Point(c).within(blocked):
                        continue
                    if k >= n - 2 and nxt_flange is not None and \
                            Point(c).buffer(P / 2 + 0.2).intersects(nxt_flange):
                        continue
                    ring.append((k, c))
        # Koerper
        m = union([extr(O[k], k * PLATE, (k + 1) * PLATE) for k in range(n)])
        cav = cells_poly(cavity_cells)
        m = m - extr(cav, -0.5, 2.0)
        m = union([m] + [tube(x, y, 2.0) for x, y in lattice_tubes(cavity_cells)])
        studs = [stud(*cell_center(i, j), n * PLATE) for i, j in new_top]
        studs += [stud(c[0], c[1], (k + 1) * PLATE) for k, c in ring]
        m = union([m] + studs)
        tiers.append(dict(name=f"ebene_{ti + 1}", m=m, z=z, h=n * PLATE,
                          cavity=len(cavity_cells), top=len(new_top), ring=len(ring)))
        for k, c in ring:
            ornament_slots.append((ti, z + (k + 1) * PLATE, c))
        top_cells = new_top
        z += n * PLATE
    assert sorted(top_cells) == [(-1, -1), (-1, 0), (0, -1), (0, 0)], \
        f"Letzte Ebene muss 2x2 Noppen fuer den Stern haben, hat {top_cells}"
    return tiers, ornament_slots, z

def build_base():
    brown = make_part(disc(BASE_R_BROWN), BASE_BROWN_H, cells_poly(BASE_CELLS_UNDER),
                      None, lattice_tubes(BASE_CELLS_UNDER),
                      [cell_center(i, j) for i, j in BASE_CELLS_UNDER])
    white = make_part(disc(BASE_R_WHITE), BASE_WHITE_H, cells_poly(BASE_CELLS_UNDER),
                      2.0, lattice_tubes(BASE_CELLS_UNDER),
                      [cell_center(i, j) for i, j in BASE_CELLS_TOP])
    return brown, white

def round_brick(h, r_cav=8.4):
    """Rund, 2x2 Noppen. Hohlraum rund (Noppen-Aussenkante 8.06 mm vom Zentrum)."""
    outline = disc(r_cav + WALL)
    return make_part(outline, h, disc(r_cav), None, [(0, 0)],
                     [(4, 4), (-4, 4), (4, -4), (-4, -4)])

def build_trunk():
    return round_brick(TRUNK_H)

def build_star(R=15.0, ratio=0.5, slab=0.5, cap=4.8, cap_scale=0.3,
               rod_d=5.6, rod_len=6.0, foot_len=2.0, foot_d=9.6):
    """Facettierter 3D-Stern auf Rundstab ("Hals") ueber dem Noppenfuss.
    - Stern: offene 5-Zack-Kontur, duenne Randplatte (2*slab) + beidseitig Pyramidenkappen.
      Die Kappenflaechen sind unter 45 Grad Ueberhang (ca. 38 Grad), die Randplatte ist
      bewusst nur ~1 mm dick, damit die steilen Zacken-Unterkanten kaum ins Gewicht fallen.
    - Stab: Zylinder rod_d, unten mit 45-Grad-Fuss (foot_*), oben in den dicken Sternkern eingelassen.
    - Fuss: rundes 2x2-Noppen-Klemmteil (Anti-Noppen unten) wie der Stamm."""
    r = R * ratio
    pts = []
    for k in range(10):
        a = math.radians(90 + k * 36)
        rad = R if k % 2 == 0 else r
        pts.append((rad * math.cos(a), rad * math.sin(a)))
    ymin = -R * math.cos(math.radians(36))
    poly = Polygon(pts)
    assert poly.is_valid
    csec = to_cs(poly)
    base = Manifold.extrude(csec, 2 * slab).translate((0, 0, -slab))
    top = Manifold.extrude(csec, cap, 0, 0.0, (cap_scale, cap_scale)).translate((0, 0, slab))
    bot = Manifold.extrude(csec, cap, 0, 0.0, (cap_scale, cap_scale)).mirror((0, 0, 1)).translate((0, 0, -slab))
    star = union([base, top, bot]).rotate((90, 0, 0))
    ped_h = 2 * PLATE
    ped = make_part(disc(8.4 + WALL), ped_h, disc(8.4), None, [(0, 0)], [])
    zc = ped_h + rod_len - ymin                 # Sternmitte ueber Fuss
    foot = Manifold.cylinder(foot_len, foot_d / 2, rod_d / 2, SEG).translate((0, 0, ped_h - 0.01))
    rod = cyl(0, 0, ped_h - 0.01, zc - 1.0 - ped_h + 0.01, rod_d / 2)
    return union([ped, foot, rod, star.translate((0, 0, zc))])

PRESENTS = [
    # name, i0, j0, w, d, h, Koerperfarbe, Schleifenfarbe
    ("A_rot_gelb",   2, -4, 2, 2, BRICK,      "rot",   "gelb"),
    ("B_gruen_gelb", -6, -3, 3, 2, BRICK,     "gruen", "gelb"),
    ("C_weiss_rot",  -3, -5, 2, 2, 2 * PLATE, "weiss", "rot"),
    ("D_rot_weiss",  3,  1, 2, 2, 4 * PLATE,  "rot",   "weiss"),
]

def build_present(i0, j0, w, d, h, bw=1.5):
    full = brick(i0, j0, w, d, h)
    cx, cy = (i0 + w // 2) * P, (j0 + d // 2) * P
    big = h + STUD_H + 2
    bands = Manifold.cube((w * P + 4, 2 * bw, big), center=False).translate((i0 * P - 2, cy - bw, -1)) + \
            Manifold.cube((2 * bw, d * P + 4, big), center=False).translate((cx - bw, j0 * P - 2, -1))
    return full, full - bands, full ^ bands

# ---- Geschenke, Clip-Variante: einfarbiger Koerper mit Nut + zwei Clip-Baender ----
CLIP_T = 0.8      # Banddicke (= Nuttiefe)
CLIP_W = 3.0      # Bandbreite
CLIP_C = 0.1      # Spiel je Seite in der Nut
CLIP_ZB = 0.4     # Bandenden stehen so hoch ueber der Unterkante
NUB_R = 0.45      # Rastnase

def _cyl_along(axis, c, r, L):
    m = Manifold.cylinder(L, r, r, 32).translate((0, 0, -L / 2))
    m = m.rotate((90, 0, 0)) if axis == "y" else m.rotate((0, 90, 0))
    return m.translate(c)

def build_present_clip(i0, j0, w, d, h, fine=False):
    """Rueckgabe: body, strip_a (laeuft in x, ein Stueck), [b1, b2] (laeuft in y, zwei Haelften), Montagelage."""
    T, W, c = CLIP_T, CLIP_W, CLIP_C
    cells = [(i, j) for i in range(i0, i0 + w) for j in range(j0, j0 + d)]
    outline = cells_poly(cells).buffer(-GAP, join_style=2)
    full = brick(i0, j0, w, d, h)
    cx, cy = (i0 + w // 2) * P, (j0 + d // 2) * P
    x0, y0, x1, y1 = outline.bounds
    ring = outline.difference(outline.buffer(-T, join_style=2))
    def band(axis, hw):
        return box(x0 - 1, cy - hw, x1 + 1, cy + hw) if axis == "x" else box(cx - hw, y0 - 1, cx + hw, y1 + 1)
    def shell(axis, hw, top=0.0):
        s = extr(ring.intersection(band(axis, hw)), CLIP_ZB if top == 0 else 0.2, h + top)
        return union([s, extr(outline.intersection(band(axis, hw)), h - T, h + top)])
    # Wand-/Deckenverstaerkung im Bereich der Nuten (damit die Nut nicht durch die Wand geht)
    zone = lambda ax: band(ax, W / 2 + c + 0.4)
    reinf = []
    for ax in ("x", "y"):
        wall = outline.difference(outline.buffer(-(WALL + T), join_style=2)).intersection(zone(ax))
        reinf.append(extr(wall, 0, h))
        reinf.append(extr(outline.intersection(zone(ax)), h - CEIL - T, h))
    body = union([full] + reinf)
    body = body - shell("x", W / 2 + c, 0.01) - shell("y", W / 2 + c, 0.01)
    # Band A laeuft in voller Dicke durch; Band B wird an der Kreuzung getrennt (zwei Haelften),
    # damit nirgends eine duenne Ueberlappung entsteht (0.4er Duese!)
    a = shell("x", W / 2)
    if fine:
        # Variante fuer 0.2er Duese: beide Baender durchgehend, an der Kreuzung je halbe Dicke (nur 0.35 mm!)
        mid = h - T / 2
        a = a - Manifold.cube((W + 0.4, y1 - y0 + 4, mid + 1.05), center=False).translate((cx - (W + 0.4) / 2, y0 - 2, -1))
        b = shell("y", W / 2) - Manifold.cube((x1 - x0 + 4, W + 0.4, h + 2 - mid + 0.05), center=False)             .translate((x0 - 2, cy - (W + 0.4) / 2, mid - 0.05))
    else:
        b = shell("y", W / 2) - Manifold.cube((W + 2 * c + 0.2, W + 2 * c + 0.2, h + 4), center=False)             .translate((cx - (W + 2 * c + 0.2) / 2, cy - (W + 2 * c + 0.2) / 2, -1))
    # Rastnasen nur bei hohen Teilen (kurze Arme sind starr)
    if h >= 6.0:
        zn = 2.0
        L = W - 0.8
        for ax, strip in (("x", a), ("y", b)):
            for sgn, lo, hi in ((1, x0 if ax == "x" else y0, None), (-1, x1 if ax == "x" else y1, None)):
                face = lo + sgn * T                  # Innenflaeche des Arms
                cen = face - sgn * (NUB_R - 0.35)    # ragt 0.35 nach innen (zum Koerper)
                if ax == "x":
                    nub = _cyl_along("y", (cen, cy, zn), NUB_R, L); dim = _cyl_along("y", (cen, cy, zn), NUB_R + 0.1, L + 0.4)
                else:
                    nub = _cyl_along("x", (cx, cen, zn), NUB_R, L); dim = _cyl_along("x", (cx, cen, zn), NUB_R + 0.1, L + 0.4)
                if ax == "x": a = a + nub
                else: b = b + nub
                body = body - dim
    if fine:
        return body, a, [b]
    halves = sorted(b.decompose(), key=lambda m: m.bounding_box()[1])
    assert len(halves) == 2, len(halves)
    return body, a, halves

# ------------------------------------------------------------------ I/O ----
def to_trimesh(m):
    me = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(me.vert_properties)[:, :3],
                           faces=np.asarray(me.tri_verts), process=False)

def export(m, rel, center_xy=None):
    """Normiert: z-min -> 0; xy-Mitte -> 0 (oder festes Zentrum)."""
    b = m.bounding_box()
    if center_xy is None:
        center_xy = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2)
    mm = m.translate((-center_xy[0], -center_xy[1], -b[2]))
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    to_trimesh(mm).export(path)
    return mm, path

SLOTS = {"weiss": 1, "rot": 2, "gruen": 3, "gelb": 4}     # Filament-Slot je Farbe im 3MF

def write_3mf(path, objects, origin=(165.0, 160.0), tmpl_name="project_settings_h2c_4filament.json"):
    """Bambu-Studio/H2C-3MF. objects = [(name, [(partname, manifold, farbe), ...], (dx, dy))].
    Jedes Objekt = ein Gegenstand mit mehreren Teilen, je Teil ein Filament-Slot (SLOTS).
    Projekteinstellungen (H2C, 4 Filamente) kommen aus vorlagen/project_settings_h2c_4filament.json."""
    import zipfile, uuid
    from xml.sax.saxutils import quoteattr
    ns = ('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
          'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
          'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p"')
    def uid():
        return str(uuid.uuid4())
    files, main_objs, items, cfg_obj, cfg_inst, obj_rels = {}, [], [], [], [], []
    nid = 0
    for oi, (name, parts_, (dx, dy)) in enumerate(objects, start=1):
        sub, comps, cfg_parts = [], [], []
        for pname, m, color in parts_:
            nid += 1
            me = m.to_mesh()
            v = np.asarray(me.vert_properties)[:, :3]
            f = np.asarray(me.tri_verts)
            vs = "".join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in v)
            ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f)
            sub.append(f'<object id="{nid}" p:UUID="{uid()}" type="model"><mesh><vertices>{vs}</vertices>'
                       f'<triangles>{ts}</triangles></mesh></object>')
            comps.append(f'<component p:path="/3D/Objects/object_{oi}.model" objectid="{nid}" '
                         f'p:UUID="{uid()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
            cfg_parts.append(
                f'<part id="{nid}" subtype="normal_part" uuid="{uid()}">'
                f'<metadata key="name" value={quoteattr(pname)}/>'
                f'<metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>'
                f'<metadata key="extruder" value="{SLOTS[color]}"/>'
                f'<mesh_stat face_count="{len(f)}" edges_fixed="0" degenerate_facets="0" '
                f'facets_removed="0" facets_reversed="0" backwards_edges="0"/></part>')
        files[f"3D/Objects/object_{oi}.model"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xml:lang="en-US" {ns}>'
            f'<metadata name="BambuStudio:3mfVersion">1</metadata><resources>{"".join(sub)}</resources>'
            f'<build/></model>')
        obj_rels.append(f'<Relationship Target="/3D/Objects/object_{oi}.model" Id="rel-{oi}" '
                        f'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>')
        nid += 1
        main_objs.append(f'<object id="{nid}" p:UUID="{uid()}" type="model"><components>{"".join(comps)}</components></object>')
        items.append(f'<item objectid="{nid}" p:UUID="{uid()}" transform="1 0 0 0 1 0 0 0 1 '
                     f'{origin[0] + dx:.4f} {origin[1] + dy:.4f} 0" printable="1"/>')
        cfg_obj.append(f'<object id="{nid}"><metadata key="name" value={quoteattr(name)}/>'
                       f'<metadata key="extruder" value="{SLOTS[parts_[0][2]]}"/>{"".join(cfg_parts)}</object>')
        cfg_inst.append(f'<model_instance><metadata key="object_id" value="{nid}"/>'
                        f'<metadata key="instance_id" value="0"/><metadata key="identify_id" value="{100 + oi}"/></model_instance>')
    files["3D/3dmodel.model"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xml:lang="en-US" {ns}>'
        '<metadata name="Application">BambuStudio-02.08.02.61</metadata>'
        '<metadata name="BambuStudio:3mfVersion">1</metadata>'
        f'<resources>{"".join(main_objs)}</resources><build p:UUID="{uid()}">{"".join(items)}</build></model>')
    files["3D/_rels/3dmodel.model.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(obj_rels) + '</Relationships>')
    files["Metadata/model_settings.config"] = (
        '<?xml version="1.0" encoding="UTF-8"?><config>' + "".join(cfg_obj) +
        '<plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value=""/>'
        '<metadata key="locked" value="false"/><metadata key="filament_map_mode" value="Auto For Flush"/>'
        + "".join(cfg_inst) + '</plate><assemble></assemble></config>')
    tmpl = os.path.join(HERE, "vorlagen", tmpl_name)
    if os.path.exists(tmpl):
        files["Metadata/project_settings.config"] = open(tmpl, encoding="utf-8").read()
    else:
        print("WARNUNG: Projektvorlage fehlt, 3MF ohne project_settings")
    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        '<Default Extension="png" ContentType="image/png"/></Types>')
    files["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        '</Relationships>')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n_ in ["[Content_Types].xml", "_rels/.rels"] + [k for k in files if k not in ("[Content_Types].xml", "_rels/.rels")]:
            z.writestr(n_, files[n_])

def bb(m):
    return m.bounding_box()

def overlap(a, b):
    A, B = bb(a), bb(b)
    if any(A[k] >= B[k + 3] or B[k] >= A[k + 3] for k in range(3)):
        return 0.0
    return (a ^ b).volume()

def main():
    random.seed(7)
    parts = {}       # name -> (manifold in Montageposition, farbe)
    print("Baue Ebenen ...")
    tiers, slots, z_top = build_tiers()
    z_base_top = BASE_BROWN_H + BASE_WHITE_H
    brown, white = build_base()
    parts["sockel_braun"] = (brown, "braun")
    parts["schneeplatte_weiss"] = (white.translate((0, 0, BASE_BROWN_H)), "weiss")
    parts["stamm_braun"] = (build_trunk().translate((0, 0, z_base_top)), "braun")
    for t in tiers:
        parts[t["name"]] = (t["m"].translate((0, 0, t["z"])), "gruen")
        print(f"  {t['name']}: Klemmflaeche {t['cavity']:2d} Noppen, Top {t['top']:2d} Noppen, "
              f"Ringnoppen {t['ring']:3d}, z={t['z']:.1f}..{t['z'] + t['h']:.1f}")
    star = build_star()
    parts["stern_gelb"] = (star.translate((0, 0, z_top)), "gelb")
    pres = {}
    for name, i0, j0, w, d, h, cb, cr in PRESENTS:
        for i in range(i0, i0 + w):
            for j in range(j0, j0 + d):
                assert (i, j) in BASE_CELLS_TOP, f"Geschenk {name}: Zelle {(i, j)} nicht auf Platte"
        full, body, rib = build_present(i0, j0, w, d, h)
        pres[name] = (full, body, rib, cb, cr)
        parts[f"geschenk_{name}"] = (full.translate((0, 0, z_base_top)), cb)

    # Schmuck (nur Vorschau/Kollision): jede 3. Ringnoppe
    orn = []
    cols = ["rot", "weiss", "gelb"]
    for idx, (ti, z, c) in enumerate(slots):
        if idx % 3 == 0:
            orn.append((f"schmuck_{idx}", ornament(c[0], c[1], z), cols[(idx // 3) % 3]))
    for n_, m_, col in orn:
        parts[n_] = (m_, col)

    # ---------- Kollisionstest Montage ----------
    print("Kollisionspruefung (Montage) ...")
    names = list(parts)
    bad = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            v = overlap(parts[names[a]][0], parts[names[b]][0])
            if v > 0.5:
                bad.append((names[a], names[b], v))
    print("  Kollisionen:", bad if bad else "keine")

    # ---------- Passung gegen NORM-Steine (4.80 / 6.51) ----------
    print("Passung gegen Norm-Stein ...")
    ref_brick = brick(2, 1, 2, 2, BRICK, tube_od=6.51, stud_d=4.80).translate((0, 0, z_base_top))
    v1 = overlap(ref_brick, parts["schneeplatte_weiss"][0])
    v1b = overlap(ref_brick, parts["stamm_braun"][0])
    ref_plate = make_part(cells_poly([(i, j) for i in range(2, 4) for j in range(-4, -2)]).buffer(-GAP, join_style=2),
                          PLATE, None, studs=[cell_center(i, j) for i in range(2, 4) for j in range(-4, -2)],
                          stud_d=4.80)
    present_on_ref = pres["A_rot_gelb"][0].translate((0, 0, PLATE))
    v2 = overlap(ref_plate, present_on_ref)
    # Norm-Stein auf die Ringnoppe des Baums:
    print(f"  Norm-Stein auf meiner Platte: {v1:.3f} mm3, Norm-Stein/Stamm: {v1b:.3f}, "
          f"mein Geschenk auf Norm-Noppen: {v2:.3f} mm3 (0 = beruehrend/frei, Zugabe in Passprobe)")

    # ---------- Export ----------
    print("Exportiere STLs ...")
    summary = []
    def rec(rel, m, color, qty=1):
        vol = m.volume() / 1000.0
        summary.append((rel.replace("\\", "/"), color, qty, round(vol, 1)))
    c0 = (0, 0)
    for nm, grp in [("sockel_braun", "20_braun"), ("stamm_braun", "20_braun"),
                    ("schneeplatte_weiss", "30_weiss"), ("stern_gelb", "40_gelb")]:
        m, _ = export(parts[nm][0], f"{grp}/{nm}.stl", None if nm in ("stamm_braun", "stern_gelb") else c0)
        rec(f"{grp}/{nm}.stl", m, parts[nm][1])
    tier_exp = {}
    for t in tiers:
        m, _ = export(t["m"], f"10_gruen_ebenen/{t['name']}.stl", c0)
        tier_exp[t["name"]] = m
        rec(f"10_gruen_ebenen/{t['name']}.stl", m, "gruen")
    for name, (full, body, rib, cb, cr) in pres.items():
        for suffix, mm in (("komplett", full), ("koerper", body), ("schleife", rib)):
            col = cb if suffix != "schleife" else cr
            if suffix == "komplett":
                col = f"{cb} (einfarbig)"
            b = full.bounding_box()
            ctr = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2)
            m, _ = export(mm, f"50_geschenke/geschenk_{name}_{suffix}.stl", ctr)
            rec(f"50_geschenke/geschenk_{name}_{suffix}.stl", m, col)
    # Geschenke: fertige 2-Farb-Objekte (Bambu-3MF, Filament-Slots vorbelegt)
    objs = []
    for k, (name, (full, body, rib, cb, cr)) in enumerate(pres.items()):
        b = full.bounding_box()
        ctr = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2)
        def norm(mm):
            return mm.translate((-ctr[0], -ctr[1], -b[2]))
        objs.append((f"geschenk_{name}", [("koerper_" + cb, norm(body), cb), ("schleife_" + cr, norm(rib), cr)],
                     ((k % 2) * 40 - 20, (k // 2) * 40 - 20)))
    write_3mf(os.path.join(OUT, "50_geschenke", "GESCHENKE_2farbig_H2C.3mf"), objs)
    write_3mf(os.path.join(OUT, "50_geschenke", "GESCHENKE_2farbig_P1S_AMS.3mf"), objs, origin=(128.0, 128.0),
              tmpl_name="project_settings_p1s_4filament.json")

    # Geschenke, Clip-Variante
    print("Clip-Geschenke ...")
    for name, i0, j0, w, d, h, cb, cr in PRESENTS:
        body, ca, bh = build_present_clip(i0, j0, w, d, h)
        full = pres[name][0]
        b = full.bounding_box()
        ctr = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2)
        m1, _ = export(body, f"50_geschenke/clip/geschenk_{name}_clip_koerper_{cb}.stl", ctr)
        m2, _ = export(ca.rotate((90, 0, 0)), f"50_geschenke/clip/geschenk_{name}_clip_band_A_{cr}.stl")
        ob = overlap(body, ca) + sum(overlap(body, x) for x in bh) + sum(overlap(ca, x) for x in bh)
        wt = [to_trimesh(m1).is_watertight, to_trimesh(m2).is_watertight]
        rec(f"50_geschenke/clip/geschenk_{name}_clip_koerper_{cb}.stl", m1, cb)
        rec(f"50_geschenke/clip/geschenk_{name}_clip_band_A_{cr}.stl", m2, cr)
        mins = []
        for k, hb in enumerate(bh, 1):
            mk, _ = export(hb.rotate((0, -90, 0)), f"50_geschenke/clip/geschenk_{name}_clip_band_B{k}_{cr}.stl")
            wt.append(to_trimesh(mk).is_watertight)
            rec(f"50_geschenke/clip/geschenk_{name}_clip_band_B{k}_{cr}.stl", mk, cr)
        print(f"  {name}: wasserdicht={all(wt)}, Ueberlapp Koerper/Baender/Haelften {ob:.3f} mm3")
        _, fa, fb = build_present_clip(i0, j0, w, d, h, fine=True)
        mfa, _ = export(fa.rotate((90, 0, 0)), f"50_geschenke/clip_0.2mm_Duese/geschenk_{name}_clip_band_A_{cr}.stl")
        mfb, _ = export(fb[0].rotate((0, -90, 0)), f"50_geschenke/clip_0.2mm_Duese/geschenk_{name}_clip_band_B_{cr}.stl")
        rec(f"50_geschenke/clip_0.2mm_Duese/geschenk_{name}_clip_band_A_{cr}.stl", mfa, cr)
        rec(f"50_geschenke/clip_0.2mm_Duese/geschenk_{name}_clip_band_B_{cr}.stl", mfb, cr)

    # Schmuck 12x
    sch = []
    for k in range(12):
        sch.append(ornament((k % 4) * 11, (k // 4) * 11, 0))
    m, _ = export(union(sch), "60_schmuck/schmuck_12x_rundplatte_1x1.stl")
    rec("60_schmuck/schmuck_12x_rundplatte_1x1.stl", m, "rot/weiss/gelb", 12)

    # Druckplatten gruen (P1S, 256x256)
    def plate(names_pos, rel):
        ms = [tier_exp[n].translate((x, y, 0)) for n, (x, y) in names_pos]
        export(union(ms), rel)
    plate([("ebene_1", (-70, -55)), ("ebene_2", (68, -58)), ("ebene_3", (-10, 60))],
          "10_gruen_ebenen/DRUCKPLATTE_P1S_A_ebene_1-3.stl")
    plate([("ebene_4", (-45, -45)), ("ebene_5", (38, -45)),
           ("ebene_6", (-45, 38)), ("ebene_7", (38, 38))],
          "10_gruen_ebenen/DRUCKPLATTE_P1S_B_ebene_4-7.stl")

    # Passprobe
    pp = []
    for k, d in enumerate((4.70, 4.78, 4.86, 4.94)):          # Noppen-Durchmesser
        pass
    cells = [(i, j) for i in range(4) for j in range(2)]
    base = make_part(cells_poly(cells).buffer(-GAP, join_style=2), PLATE,
                     cells_poly(cells).buffer(-GAP - WALL, join_style=2), 2.0,
                     lattice_tubes(cells), [])
    base = union([base] + [stud(*cell_center(i, j), PLATE, d=(4.70, 4.78, 4.86, 4.94)[i]) for i, j in cells])
    pp.append(base.translate((-16, 20, 0)))
    for k, od in enumerate((6.40, 6.48, 6.56)):                 # Roehren-Aussendurchmesser
        c = [(0, 0), (1, 0), (0, 1), (1, 1)]
        t = make_part(cells_poly(c).buffer(-GAP, join_style=2), PLATE,
                      cells_poly(c).buffer(-GAP - WALL, join_style=2), 2.0,
                      lattice_tubes(c), [], tube_od=od)
        pp.append(t.translate((-24 + k * 20, 0, 0)))
    for k, idd in enumerate((4.75, 4.85, 4.95)):                # 1x1-Rund Innenloch
        pp.append(ornament(-20 + k * 12, -16, 0, id_=idd))
    export(union(pp), "00_passprobe/passprobe_stud_roehre_rund.stl")

    # Gesamtmontage
    asm = union([m for m, _ in parts.values()])
    export(asm, "99_vorschau/baum_komplett_montiert.stl", c0)

    with open(os.path.join(OUT, "teileliste.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["datei", "farbe", "anzahl", "volumen_massiv_cm3"])
        w.writerows(summary)
    tot = sum(r[3] * r[2] for r in summary if "komplett" not in r[0] and "DRUCK" not in r[0])
    print(f"Massives Gesamtvolumen (ohne Doppelungen): {tot:.0f} cm3")
    bbx = asm.bounding_box()
    print("Montage-Masse: Breite %.1f mm, Tiefe %.1f mm, Hoehe %.1f mm" %
          (bbx[3] - bbx[0], bbx[4] - bbx[1], bbx[5] - bbx[2]))
    return parts, tiers

if __name__ == "__main__":
    main()

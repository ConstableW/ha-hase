"""Vorschau-Renderer (Painter's Algorithm, matplotlib). Aufruf: python render.py"""
import os, sys, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import trimesh
import build

COL = {"gruen": (0.10, 0.50, 0.22), "braun": (0.40, 0.24, 0.12), "weiss": (0.93, 0.93, 0.93),
       "gelb": (0.97, 0.78, 0.10), "rot": (0.78, 0.10, 0.10)}

def render(items, path, az=35, el=22, explode=0.0, size=(7, 9)):
    ca, sa = math.cos(math.radians(az)), math.sin(math.radians(az))
    ce, se = math.cos(math.radians(el)), math.sin(math.radians(el))
    light = np.array([0.4, -0.5, 0.8]); light /= np.linalg.norm(light)
    V, F, C = [], [], []
    for k, (m, color) in enumerate(items):
        t = build.to_trimesh(m)
        v = t.vertices.copy(); v[:, 2] += k * explode
        # Rotation um z (az), dann Kipp um x (el)
        x = v[:, 0] * ca - v[:, 1] * sa
        y = v[:, 0] * sa + v[:, 1] * ca
        z = v[:, 2]
        xs, ys, depth = x, y * se + z * ce, y * ce - z * se   # depth: kleiner = naeher
        f = t.faces
        n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        n /= (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)
        # Kameraseite: Blickrichtung in Weltkoordinaten
        view = np.array([sa * ce * -1 * -1, -ca * ce, se])  # grob
        view = np.array([ -sa * ce, ca * ce * -1, se]) if False else np.array([sa * ce, -ca * ce, se])
        facing = n @ view > -0.0
        shade = 0.35 + 0.65 * np.clip(n @ light, 0, 1)
        base = np.array(COL[color])
        cols = np.clip(base[None, :] * shade[:, None], 0, 1)
        tri = np.stack([xs[f], ys[f]], axis=2)
        d = depth[f].mean(axis=1)
        V.append(tri[facing]); C.append(cols[facing]); F.append(d[facing])
    V = np.concatenate(V); C = np.concatenate(C); D = np.concatenate(F)
    order = np.argsort(-D)    # fern zuerst
    fig = plt.figure(figsize=size, dpi=160)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.add_collection(PolyCollection(V[order], facecolors=C[order], edgecolors="none", linewidths=0))
    ax.set_xlim(V[:, :, 0].min() - 5, V[:, :, 0].max() + 5)
    ax.set_ylim(V[:, :, 1].min() - 5, V[:, :, 1].max() + 5)
    ax.set_aspect("equal"); ax.axis("off")
    fig.patch.set_facecolor("#f4efe6")
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)

if __name__ == "__main__":
    parts, tiers = build.main()
    items = list(parts.values())
    os.makedirs(os.path.join(build.HERE, "vorschau"), exist_ok=True)
    render(items, os.path.join(build.HERE, "vorschau", "montiert.png"))
    core = [parts[n] for n in ["sockel_braun", "schneeplatte_weiss", "stamm_braun"]
            + [t["name"] for t in tiers] + ["stern_gelb"]]
    render(core, os.path.join(build.HERE, "vorschau", "explosion.png"), explode=22, size=(7, 12))


def zrender(m, path, az=25, el=12, W=520, H=700, color=(0.97, 0.78, 0.10)):
    """Echter Z-Buffer-Renderer (fuer Einzelteile; langsam bei sehr vielen Dreiecken)."""
    from PIL import Image
    t = build.to_trimesh(m); v = t.vertices.copy(); f = t.faces
    ca, sa = math.cos(math.radians(az)), math.sin(math.radians(az))
    ce, se = math.cos(math.radians(el)), math.sin(math.radians(el))
    x = v[:, 0] * ca - v[:, 1] * sa; y = v[:, 0] * sa + v[:, 1] * ca; z = v[:, 2]
    X = x; Y = y * se + z * ce; D = y * ce - z * se
    s = min(W / (X.max() - X.min()), H / (Y.max() - Y.min())) * 0.85
    px = (X - (X.max() + X.min()) / 2) * s + W / 2; py = H / 2 - (Y - (Y.max() + Y.min()) / 2) * s
    n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]); n /= (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)
    L = np.array([0.4, -0.5, 0.8]); L /= np.linalg.norm(L)
    sh = 0.35 + 0.65 * np.clip(n @ L, 0, 1)
    img = np.ones((H, W, 3)) * np.array([0.957, 0.937, 0.902]); zb = np.full((H, W), 1e9)
    for i, tri in enumerate(f):
        xs = px[tri]; ys = py[tri]; ds = D[tri]
        x0, x1 = int(max(xs.min(), 0)), int(min(xs.max() + 1, W - 1)); y0, y1 = int(max(ys.min(), 0)), int(min(ys.max() + 1, H - 1))
        if x1 <= x0 or y1 <= y0: continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + .5, np.arange(y0, y1 + 1) + .5)
        d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(d) < 1e-9: continue
        a = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
        b = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
        c = 1 - a - b; inside = (a >= 0) & (b >= 0) & (c >= 0)
        dep = a * ds[0] + b * ds[1] + c * ds[2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]; msk = inside & (dep < sub)
        sub[msk] = dep[msk]; img[y0:y1 + 1, x0:x1 + 1][msk] = np.array(color) * sh[i]
    Image.fromarray((img * 255).astype('uint8')).save(path)

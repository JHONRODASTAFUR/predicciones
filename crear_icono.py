"""Genera icono.ico (acceso directo) y web/icono.png (ventana) con Pillow."""
import math
from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent


def dibujar(t=512):
    im = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, t - 1, t - 1], radius=t * 0.22, fill=(10, 15, 28, 255))
    # barras de probabilidad (local / empate / visita)
    base_y, ancho = t * 0.86, t * 0.13
    for i, (alto, color) in enumerate([(0.30, (59, 130, 246)), (0.18, (139, 151, 173)), (0.24, (249, 115, 22))]):
        x0 = t * 0.18 + i * (ancho + t * 0.05)
        d.rounded_rectangle([x0, base_y - alto * t, x0 + ancho, base_y], radius=t * 0.03, fill=color + (255,))
    # balón
    cx, cy, r = t * 0.64, t * 0.38, t * 0.25
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(231, 236, 246, 255), outline=(10, 15, 28, 255), width=int(t * 0.02))
    pent = [(cx + r * 0.42 * math.cos(math.radians(-90 + 72 * k)), cy + r * 0.42 * math.sin(math.radians(-90 + 72 * k)))
            for k in range(5)]
    d.polygon(pent, fill=(10, 15, 28, 255))
    for (px, py) in pent:
        ang = math.atan2(py - cy, px - cx)
        d.line([(px, py), (cx + r * 0.97 * math.cos(ang), cy + r * 0.97 * math.sin(ang))], fill=(10, 15, 28, 255),
               width=int(t * 0.025))
    return im


if __name__ == "__main__":
    im = dibujar()
    im.save(BASE / "icono.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    im.resize((128, 128), Image.LANCZOS).save(BASE / "web" / "icono.png")
    print("Icono creado")

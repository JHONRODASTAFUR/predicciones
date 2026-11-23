"""Genera icono.ico (acceso directo) y web/icono.png (ventana) con Pillow: la R de Rodas.

Es el mismo dibujo de web/logo-r.svg, pasado a puntos para poder guardarlo como icono de Windows.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent
AZUL, TURQUESA, FONDO = (7, 139, 255), (32, 216, 232), (7, 13, 26)
INCLINACION = math.tan(math.radians(10))      # la R va inclinada hacia delante, como en el SVG (skewX(-10))


def _curva(p0, p1, p2, p3, pasos=18):
    """Puntos de una curva Bézier cúbica."""
    out = []
    for k in range(1, pasos + 1):
        t = k / pasos
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _r():
    """Contorno exterior, hueco interior y acento de la R, en la rejilla de 64 x 64 del SVG."""
    fuera = [(15, 10), (37.5, 10)] + _curva((37.5, 10), (46.6, 10), (52, 15.2), (52, 23.2)) \
        + _curva((52, 23.2), (52, 30), (48.4, 34.5), (42.6, 36.3)) \
        + [(52.5, 54), (40.6, 54), (32, 37.8), (26, 37.8), (26, 54), (15, 54)]
    dentro = [(26, 19.4), (26, 28.6), (36.6, 28.6)] + _curva((36.6, 28.6), (40, 28.6), (41.7, 26.8), (41.7, 24)) \
        + _curva((41.7, 24), (41.7, 21.2), (40, 19.4), (36.6, 19.4))
    acento = [(5, 44), (11.5, 44), (9.4, 54), (2.9, 54)]
    return fuera, dentro, acento


def dibujar(t=512):
    e = t / 64

    def pt(p):
        return ((p[0] - INCLINACION * p[1] + 5.6) * e, p[1] * e)

    fuera, dentro, acento = _r()
    im = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, t - 1, t - 1], radius=t * 0.22, fill=FONDO + (255,))
    d.rounded_rectangle([t * 0.012, t * 0.012, t * 0.988, t * 0.988], radius=t * 0.21, outline=(24, 130, 210, 255),
                        width=max(1, int(t * 0.012)))
    # degradado azul eléctrico → turquesa (diagonal) y la R como máscara
    grad = Image.linear_gradient("L").rotate(-45, resample=Image.BICUBIC, expand=False).resize((t, t))
    grad = Image.merge("RGBA", [grad.point(lambda v, i=i: int(AZUL[i] + (TURQUESA[i] - AZUL[i]) * v / 255)) for i in range(3)]
                       + [Image.new("L", (t, t), 255)])
    mascara = Image.new("L", (t, t), 0)
    m = ImageDraw.Draw(mascara)
    m.polygon([pt(p) for p in fuera], fill=255)
    m.polygon([pt(p) for p in dentro], fill=0)
    m.polygon([pt(p) for p in acento], fill=230)
    im.paste(grad, (0, 0), mascara)
    return im


if __name__ == "__main__":
    im = dibujar()
    im.save(BASE / "icono.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    im.resize((128, 128), Image.LANCZOS).save(BASE / "web" / "icono.png")
    print("Icono creado")

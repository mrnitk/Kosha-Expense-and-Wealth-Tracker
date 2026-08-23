"""Generate the macOS app icon (Kosha.icns) from scratch.

The repo ships no image assets, so the icon is drawn with Qt (already a
dependency) and assembled into an .icns with the system's iconutil. Run from
the repo root inside the macOS venv:

    .venv-mac/bin/python installer/macos/make_icns.py

Writes installer/macos/Kosha.icns. Only needs re-running if the artwork changes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
)

HERE = Path(__file__).resolve().parent
OUT = HERE / "Kosha.icns"

# macOS icons are drawn inside a rounded square that leaves a margin around the
# canvas; ~10% each side matches the platform's optical sizing.
MARGIN = 0.10
RADIUS = 0.225          # corner radius as a fraction of the squircle's side
TOP = QColor("#2E7D6B")  # deep teal — "treasury" without looking like a bank logo
BOTTOM = QColor("#1B4D45")
GLYPH = QColor("#F5F1E6")


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    inset = size * MARGIN
    box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)

    grad = QLinearGradient(box.topLeft(), box.bottomRight())
    grad.setColorAt(0.0, TOP)
    grad.setColorAt(1.0, BOTTOM)

    path = QPainterPath()
    path.addRoundedRect(box, box.width() * RADIUS, box.height() * RADIUS)
    p.fillPath(path, grad)

    # A "K" over a rupee-style double bar: expenses and wealth, in one mark.
    font = QFont()
    font.setPixelSize(int(box.height() * 0.56))
    font.setWeight(QFont.DemiBold)
    p.setFont(font)
    p.setPen(GLYPH)
    glyph_box = QRectF(box.x(), box.y() - box.height() * 0.04, box.width(), box.height())
    p.drawText(glyph_box, Qt.AlignCenter, "K")

    bar_w = box.width() * 0.42
    bar_h = max(1.0, box.height() * 0.045)
    bar_x = box.x() + (box.width() - bar_w) / 2
    bar_y = box.y() + box.height() * 0.72
    for i in range(2):
        p.fillRect(
            QRectF(bar_x, bar_y + i * bar_h * 2.1, bar_w, bar_h),
            QColor(GLYPH.red(), GLYPH.green(), GLYPH.blue(), 235 - i * 60),
        )

    p.end()
    return img


def main() -> int:
    if sys.platform != "darwin":
        print("iconutil is macOS-only", file=sys.stderr)
        return 1
    if shutil.which("iconutil") is None:
        print("iconutil not found (install the Xcode command line tools)", file=sys.stderr)
        return 1

    QGuiApplication([])  # QImage/QPainter text rendering needs a GUI application

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Kosha.iconset"
        iconset.mkdir()
        # The sizes `iconutil` expects: each base size plus its @2x retina twin.
        for base in (16, 32, 128, 256, 512):
            render(base).save(str(iconset / f"icon_{base}x{base}.png"))
            render(base * 2).save(str(iconset / f"icon_{base}x{base}@2x.png"))
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)],
            check=True,
        )

    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

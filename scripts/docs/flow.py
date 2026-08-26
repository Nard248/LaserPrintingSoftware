"""Vertical flow helper: cursor tracks the TOP of the next block."""
import sys; sys.path.insert(0, ".")
from style import *

class Flow:
    def __init__(self, ax, x, w, top, gap=0.42):
        self.ax, self.x, self.w, self.y, self.gap = ax, x, w, top, gap
        self.last = None

    def block(self, h, edge, face, head, code="", note="", side="",
              hs=10, cs=8.4, connect=True, gap=None):
        ax, x, w = self.ax, self.x, self.w
        g = self.gap if gap is None else gap
        if connect and self.last is not None:
            arrow(ax, (x+w/2, self.y+g), (x+w/2, self.y+0.02))
        y = self.y - h
        box(ax, x, y, w, h, edge, face, "", "")
        ax.text(x+0.22, y+h-0.19, head, ha="left", va="top", fontsize=hs,
                color=INK, weight="bold", zorder=3, linespacing=1.25)
        if code:
            off = 0.30 + 0.20*head.count("\n")
            ax.text(x+0.22, y+h-0.19-off, code, ha="left", va="top", fontsize=cs,
                    color="#1e4620", family="DejaVu Sans Mono", zorder=3, linespacing=1.5)
        if note:
            ax.text(x+w-0.22, y+0.14, note, ha="right", va="bottom", fontsize=7.8,
                    color=MUTE, style="italic", zorder=3, linespacing=1.3)
        if side:
            ax.text(x+w+0.18, y+h/2, side, ha="left", va="center", fontsize=7.6,
                    color=MUTE, style="italic", zorder=3, linespacing=1.35)
        self.last = (y, h)
        self.y = y - g
        return y

    def boundary(self, label_text="TRUST\nBOUNDARY", above="", below="", pad=0.55):
        ax, x, w = self.ax, self.x, self.w
        y = self.y - 0.10
        ax.plot([x-0.75, x+w+0.75], [y, y], color=INK, lw=2.6, ls=(0, (6, 3)), zorder=6)
        label(ax, x+w+0.82, y, label_text, size=8.8, color=INK, ha="left", bold=True)
        if above:
            label(ax, x-0.70, y+0.17, above, size=7.8, color=COG, ha="left", italic=True)
        if below:
            label(ax, x-0.70, y-0.17, below, size=7.8, color=DET, ha="left",
                  va="top", italic=True)
        self.y = y - pad
        self.last = None

    def gap_only(self, dy):
        self.y -= dy

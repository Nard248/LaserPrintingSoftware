"""Shared visual language for all labgate documentation diagrams."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# Semantic palette — colour always means the same thing across every figure
COG,  COG_BG  = "#3b6fb6", "#dce7f5"   # AI / untrusted cognition
DET,  DET_BG  = "#2e8b57", "#d8ecdf"   # deterministic / trusted platform
HUM,  HUM_BG  = "#c8881f", "#f6e7c8"   # human action
SAFE, SAFE_BG = "#b23b3b", "#f3d6d6"   # hardware / safety
STOR, STOR_BG = "#6b4c9a", "#e6dcf2"   # storage
GREY, GREY_BG = "#7a7a7a", "#ececec"   # inert / external
INK, MUTE     = "#222222", "#666666"


def box(ax, x, y, w, h, edge, face, title, sub="", ts=11, ss=8.2,
        bold=True, tcol=None, lsp=1.25, radius=0.02):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                 boxstyle=f"round,pad=0.008,rounding_size={radius}",
                 lw=1.6, edgecolor=edge, facecolor=face, zorder=2))
    if sub:
        ax.text(x + w/2, y + h*0.66, title, ha="center", va="center", zorder=3,
                fontsize=ts, color=tcol or INK, weight="bold" if bold else "normal",
                linespacing=lsp)
        ax.text(x + w/2, y + h*0.27, sub, ha="center", va="center", zorder=3,
                fontsize=ss, color=MUTE, linespacing=1.3)
    else:
        ax.text(x + w/2, y + h/2, title, ha="center", va="center", zorder=3,
                fontsize=ts, color=tcol or INK, weight="bold" if bold else "normal",
                linespacing=lsp)


def arrow(ax, p1, p2, color=MUTE, lw=1.5, style="-|>", ms=14, ls="-", z=4):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=ms,
                 lw=lw, color=color, linestyle=ls, zorder=z,
                 shrinkA=1, shrinkB=1))


def label(ax, x, y, text, size=8, color=MUTE, ha="center", va="center",
          italic=False, bold=False, rot=0, bg=None):
    kw = {}
    if bg:
        kw["bbox"] = dict(boxstyle="round,pad=0.25", fc=bg, ec="none", alpha=0.95)
    ax.text(x, y, text, ha=ha, va=va, fontsize=size, color=color, zorder=5,
            style="italic" if italic else "normal",
            weight="bold" if bold else "normal", rotation=rot, linespacing=1.3, **kw)


def canvas(w, h, xlim, ylim):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(*xlim); ax.set_ylim(*ylim); ax.axis("off")
    return fig, ax


def save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def legend(ax, x, y, items, size=8.2, dx=1.75, bw=0.26, bh=0.26):
    for name, edge, face in items:
        box(ax, x, y, bw, bh, edge, face, "")
        label(ax, x + bw + 0.09, y + bh/2, name, size=size, ha="left", color=INK)
        x += dx

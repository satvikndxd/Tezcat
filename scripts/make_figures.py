"""Generate README/docs figures from real (deterministic) simulation runs.

Palette validated with the dataviz six-checks validator on the dark surface:
  categorical: #c98500 (amber) / #199e70 (aqua) / #9085e9 (violet)  — ALL PASS
  polarity:    #199e70 (profit) / #e66767 (loss) — PASS w/ CVD warn, mitigated
               by signed direct value labels on every bar.
Usage: .venv/bin/python scripts/make_figures.py
"""

import sys

sys.path.insert(0, ".")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tezcat.engine.ecology import EcologyEngine
from tezcat.experiments.presets import build_config

# ---------------------------------------------------------------- theme
SURFACE = "#101010"
PANEL = "#101010"
GRID = "#242424"
INK = "#e8e6df"
INK_MUTED = "#8a8a8a"
AMBER = "#c98500"
AQUA = "#199e70"
VIOLET = "#9085e9"
LOSS = "#e66767"
CRISIS_BAND = (0.90, 0.28, 0.28, 0.10)
RECOVERY_BAND = (0.10, 0.62, 0.44, 0.10)

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": PANEL,
    "savefig.facecolor": SURFACE,
    "font.family": "monospace",
    "font.monospace": ["DejaVu Sans Mono"],
    "text.color": INK,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK_MUTED,
    "xtick.color": INK_MUTED,
    "ytick.color": INK_MUTED,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 9,
})


def run(preset, seed=42):
    eng = EcologyEngine(f"fig_{preset}", build_config(preset), seed)
    while not eng.done:
        eng.step()
    return eng


def regime_bands(ax, snapshots):
    """Shade crisis/recovery segments behind the data."""
    start, current = None, None
    for s in snapshots + [{"step": snapshots[-1]["step"] + 1, "regime": "end"}]:
        r = s["regime"]
        if r != current:
            if current in ("crisis", "recovery") and start is not None:
                color = CRISIS_BAND if current == "crisis" else RECOVERY_BAND
                ax.axvspan(start, s["step"], color=color, lw=0, zorder=0)
            start, current = s["step"], r


def style(ax, ylabel):
    ax.set_ylabel(ylabel, fontsize=8)
    ax.margins(x=0.01)
    ax.tick_params(length=0)


# ================================================================ fig 1
def fig_flash_crash(eng):
    snaps = eng.snapshots
    steps = [s["step"] for s in snaps]
    price = [s["last_price"] for s in snaps]
    spread = [s["spread"] if s["spread"] is not None else float("nan") for s in snaps]
    depth = [s["bid_depth"] + s["ask_depth"] for s in snaps]

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(9.6, 6.4), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1, 1], "hspace": 0.12})

    for ax in (ax1, ax2, ax3):
        regime_bands(ax, snaps)

    ax1.plot(steps, price, color=AMBER, lw=1.6, zorder=3)
    style(ax1, "PRICE")
    ax2.plot(steps, spread, color=AQUA, lw=1.2, zorder=3)
    style(ax2, "SPREAD")
    ax3.plot(steps, depth, color=VIOLET, lw=1.2, zorder=3)
    style(ax3, "BOOK DEPTH")
    ax3.set_xlabel("STEP", fontsize=8)

    # shock markers; labels stacked to the LEFT of the shock cluster (shocks
    # fire 15 steps apart, so per-marker labels would collide)
    labels = {"whale_dump": "1 WHALE SELL PROGRAM", "mm_pullout": "2 MM WITHDRAWAL",
              "panic_sentiment": "3 PANIC SENTIMENT"}
    y_top = max(price)
    for i, ev in enumerate(eng.shocks.events):
        for ax in (ax1, ax2, ax3):
            ax.axvline(ev.step, color=AMBER, lw=0.8, ls=(0, (4, 3)), alpha=0.65, zorder=2)
        ax1.annotate(labels.get(ev.shock_id, ev.shock_type.upper()),
                     xy=(ev.step, y_top), xytext=(ev.step - 30, y_top - 1 - 3.4 * i),
                     fontsize=7, color=AMBER, ha="right", va="top")

    # trough label to the left of the low
    trough = min(range(len(price)), key=lambda i: price[i])
    ax1.annotate(f"{min(price):.2f} (−{(100 - min(price)):.0f}%) ",
                 xy=(steps[trough], price[trough]), fontsize=7.5, color=INK,
                 ha="right", va="center")

    # regime words centered in the first crisis / recovery bands
    def first_band(regime):
        seg = [s["step"] for s in snaps if s["regime"] == regime]
        return (seg[0] + seg[len(seg) // 2]) / 2 if seg else None

    cx, rx = first_band("crisis"), first_band("recovery")
    if cx:
        ax1.annotate("CRISIS", xy=(cx, y_top + 1), fontsize=7, color="#e66767",
                     ha="center", va="bottom")
    if rx:
        ax1.annotate("RECOVERY", xy=(rx, y_top + 1), fontsize=7, color=AQUA,
                     ha="center", va="bottom")
    ax1.set_ylim(top=y_top + 5)

    fig.suptitle("FLASH CRASH PRESET — SEED 42 · PRICE, SPREAD, LIQUIDITY",
                 fontsize=10, color=INK, x=0.5, y=0.965)
    fig.text(0.5, 0.925, "whale sell program → market-maker withdrawal → panic → V-shaped recovery",
             fontsize=8, color=INK_MUTED, ha="center")
    fig.savefig("docs/img/flash_crash.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


# ================================================================ fig 2
def fig_presets(engines):
    fig, ax = plt.subplots(figsize=(9.6, 4.2))
    series = [("bubble_formation", "BUBBLE FORMATION", AMBER),
              ("flash_crash", "FLASH CRASH", AQUA),
              ("stable_baseline", "STABLE BASELINE", VIOLET)]
    for pid, label, color in series:
        snaps = engines[pid].snapshots
        ax.plot([s["step"] for s in snaps], [s["last_price"] for s in snaps],
                color=color, lw=1.6, label=label)
        last = snaps[-1]
        ax.annotate(f"{label}  {last['last_price']:.1f}",
                    xy=(last["step"], last["last_price"]),
                    xytext=(6, 0), textcoords="offset points",
                    fontsize=7.5, color=color, va="center")
    ax.axhline(100, color=GRID, lw=0.8)
    style(ax, "PRICE")
    ax.set_xlabel("STEP", fontsize=8)
    ax.set_xlim(0, 2750)  # room for end labels
    ax.legend(loc="upper left", frameon=False, fontsize=7.5, labelcolor=INK)
    ax.set_title("THREE PRESETS, ONE ENGINE — EMERGENT PRICE PATHS (SEED 42)",
                 fontsize=10, color=INK, pad=12)
    fig.savefig("docs/img/presets.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


# ================================================================ fig 3
def fig_pnl(eng):
    rep = eng.build_report()
    items = sorted(rep["agent_pnl_by_type"].items(), key=lambda kv: kv[1]["total_pnl"])
    names = [k.replace("_", " ").upper() for k, _ in items]
    vals = [v["total_pnl"] for _, v in items]

    fig, ax = plt.subplots(figsize=(9.6, 3.4))
    colors = [AQUA if v >= 0 else LOSS for v in vals]
    bars = ax.barh(names, vals, color=colors, height=0.55, zorder=3)
    ax.axvline(0, color=INK_MUTED, lw=0.8)
    ax.grid(axis="y", visible=False)
    ax.margins(x=0.15)
    ax.tick_params(length=0)
    ax.set_xlabel("TOTAL PnL AFTER RUN ($)", fontsize=8)
    for bar, v in zip(bars, vals):  # signed direct labels = CVD secondary encoding
        ax.annotate(f"{v:+,.0f}", xy=(v, bar.get_y() + bar.get_height() / 2),
                    xytext=(6 if v >= 0 else -6, 0), textcoords="offset points",
                    fontsize=8, color=INK, va="center",
                    ha="left" if v >= 0 else "right")
    ax.set_title("WHO PAID FOR THE FLASH CRASH — PnL BY STRATEGY (SEED 42)",
                 fontsize=10, color=INK, pad=12)
    fig.text(0.5, 0.895, "dip-buying mean-reverters profit · panic-selling retail and trend-chasers lose",
             fontsize=8, color=INK_MUTED, ha="center")
    fig.savefig("docs/img/agent_pnl.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    engines = {p: run(p) for p in ("stable_baseline", "flash_crash", "bubble_formation")}
    fig_flash_crash(engines["flash_crash"])
    fig_presets(engines)
    fig_pnl(engines["flash_crash"])
    print("figures written to docs/img/")

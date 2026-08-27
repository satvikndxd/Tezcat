#!/usr/bin/env python3
"""Vector architecture/pipeline diagrams for the Tezcat IEEE paper."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parent
plt.rcParams.update({"font.family": "DejaVu Sans", "savefig.bbox": "tight"})

INK = "#1b2430"
EDGE = "#4a5568"
FILL = {"ui": "#eaf2f8", "core": "#fdf2e9", "research": "#eafaf1",
        "ext": "#f4ecf7", "ops": "#fef9e7", "warn": "#fdedec"}


def box(ax, x, y, w, h, text, fill="#ffffff", fs=6.4, bold=False, ec=EDGE):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012",
                                fc=fill, ec=ec, lw=0.7))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=INK,
            fontweight="bold" if bold else "normal", linespacing=1.25)


def arrow(ax, x1, y1, x2, y2, style="-|>", lw=0.8, color=EDGE, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=7, lw=lw, color=color,
                                 linestyle=ls, shrinkA=1, shrinkB=1))


def canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


# ---------------------------------------------------------------------------
def fig_architecture():
    """Render a legible, layered systems architecture for IEEE publication."""
    fig, ax = canvas(7.16, 5.20)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    def panel(x, y, w, h, title, fill, ec=EDGE, ls="-"):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                                    boxstyle="round,pad=0.008",
                                    fc=fill, ec=ec, lw=0.9, linestyle=ls))
        ax.text(x + 0.014, y + h - 0.025, title, ha="left", va="center",
                fontsize=7.4, fontweight="bold", color=INK)

    def node(x, y, w, h, text, fill="#ffffff", fs=6.7, bold=False,
             ec=EDGE, ls="-"):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                                    boxstyle="round,pad=0.006",
                                    fc=fill, ec=ec, lw=0.65, linestyle=ls))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, color=INK,
                fontweight="bold" if bold else "normal", linespacing=1.18)

    def conn(x1, y1, x2, y2, color=EDGE, ls="-", lw=0.85, style="-|>"):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                     mutation_scale=8, lw=lw, color=color,
                                     linestyle=ls, shrinkA=2, shrinkB=2))

    ax.text(0.01, 0.992, "TEZCAT ARCHITECTURE",
            ha="left", va="top", fontsize=9.4, fontweight="bold", color=INK)
    ax.text(0.99, 0.992, "deterministic laboratory  |  simulation ≠ forecasting",
            ha="right", va="top", fontsize=7.0, color="#7b241c", style="italic")

    # Entry points and the API/control surface.
    panel(0.02, 0.845, 0.96, 0.105,
          "RESEARCH INTERFACES  via  CONTROL PLANE", FILL["ui"])
    node(0.04, 0.862, 0.145, 0.045, "Researcher\nCLI / dashboard", FILL["ui"], fs=6.8, bold=True)
    node(0.205, 0.862, 0.235, 0.045, "React/Vite dashboard\nBuild · Research · Lab · Markets · Ops", fs=5.6)
    node(0.465, 0.862, 0.195, 0.045, "FastAPI control plane\n/api/*", fs=6.8, bold=True)
    node(0.685, 0.862, 0.275, 0.045,
         "Registry · run manager · research API\nTradeOps queue", fs=6.1)
    conn(0.185, 0.885, 0.205, 0.885)
    conn(0.44, 0.885, 0.465, 0.885)
    conn(0.66, 0.885, 0.685, 0.885)

    # Deterministic kernel with a left-to-right causal path.
    panel(0.02, 0.495, 0.96, 0.305,
          "TEZCAT ECOLOGY ENGINE  |  deterministic step loop", FILL["core"])
    node(0.04, 0.625, 0.18, 0.105,
         "ENVIRONMENT\nSHOCKS: whale · MM pull\nREGIMES: stable · crisis · recovery",
         FILL["warn"], fs=5.2, bold=False)
    node(0.245, 0.625, 0.22, 0.105,
         "AGENTS + MEMORY\n5 heterogeneous types\nNoise · retail · momentum\nMemory: fear · confidence",
         FILL["ui"], fs=5.2, bold=False)
    node(0.495, 0.625, 0.22, 0.105,
         "LIMIT ORDER BOOK\nPrice–time priority\nTick snapping · partial fills\nMatching engine",
         "#ffffff", fs=5.2, bold=False)
    node(0.745, 0.625, 0.215, 0.105,
         "PORTFOLIO + RISK\nReservations · margin\nLiquidation as order flow\nNo negative balances",
         FILL["research"], fs=5.2, bold=False)
    node(0.04, 0.535, 0.285, 0.05,
         "Event log  |  hash chain\nReplay · checkpoints · forks",
         "#ffffff", fs=5.4)
    node(0.345, 0.535, 0.285, 0.05,
         "Metrics  |  returns · spread · depth\nimbalance · drawdown",
         "#ffffff", fs=5.4)
    node(0.65, 0.535, 0.31, 0.05,
         "Endogenous price\nno external series in normal runs",
         FILL["research"], fs=5.4, bold=True)
    conn(0.18, 0.845, 0.18, 0.80)
    conn(0.56, 0.845, 0.56, 0.80)
    conn(0.22, 0.677, 0.245, 0.677)
    conn(0.465, 0.677, 0.495, 0.677)
    conn(0.715, 0.677, 0.745, 0.677)
    conn(0.605, 0.625, 0.605, 0.585)
    conn(0.86, 0.625, 0.86, 0.585)

    # Scientific workflow and provenance.
    panel(0.02, 0.315, 0.96, 0.135,
          "RESEARCH + PROVENANCE LAYER", FILL["research"])
    node(0.04, 0.335, 0.21, 0.052,
         "ExperimentVersion\ncanonical JSON · seed · SHA-256", fs=5.0, bold=False)
    node(0.27, 0.335, 0.21, 0.052,
         "BatchRunner / TradeOps\nreplicate · resume · serial ≡ parallel", fs=5.0)
    node(0.50, 0.335, 0.21, 0.052,
         "Analysis / calibration\nbootstrap · permutation · OOS", fs=5.0)
    node(0.73, 0.335, 0.21, 0.052,
         "Reports / replay\nartifacts · manifests · reproduce", fs=5.0, bold=False)
    conn(0.56, 0.495, 0.56, 0.45)
    conn(0.25, 0.361, 0.27, 0.361)
    conn(0.48, 0.361, 0.50, 0.361)
    conn(0.71, 0.361, 0.73, 0.361)

    # Outward-facing observation and strategy layers.
    panel(0.02, 0.155, 0.31, 0.115,
          "EXTERNAL DATA  |  read-only", FILL["ext"])
    node(0.04, 0.172, 0.125, 0.038, "Kalshi", fs=6.3)
    node(0.18, 0.172, 0.135, 0.038, "Polymarket", fs=6.3)
    ax.text(0.177, 0.161, "datasets · signatures",
            ha="center", va="bottom", fontsize=5.6, color=INK)
    panel(0.35, 0.155, 0.29, 0.115,
          "SYNTHETIC WORLDS", FILL["core"])
    node(0.37, 0.172, 0.25, 0.038,
         "world hash · ecology fingerprint\nquote / trade stream", fs=5.1, bold=False)
    panel(0.66, 0.155, 0.32, 0.115,
          "NAUTILUS  |  BACKTEST ONLY", FILL["research"])
    node(0.68, 0.172, 0.13, 0.038, "QuoteTick /\nTradeTick", fs=5.7)
    node(0.82, 0.172, 0.14, 0.038, "Strategies → metrics\nobserver only", fs=5.1, bold=False)
    conn(0.13, 0.315, 0.13, 0.27, color="#7d3c98", ls="--")
    conn(0.33, 0.212, 0.35, 0.212, color="#7d3c98", ls="--")
    conn(0.64, 0.212, 0.66, 0.212)

    # S5 and DevOps are intentionally separated and status-labelled.
    panel(0.02, 0.025, 0.64, 0.095,
          "RESEARCH CONTROL PLANE (S5)", FILL["research"])
    ax.text(0.34, 0.058,
            "dataset → forecast → portfolio → world → backtest → risk → report\nhash-linked reproduction",
            ha="center", va="center", fontsize=5.4, color=INK)
    panel(0.68, 0.025, 0.30, 0.095,
          "DEVOPS STATUS", FILL["ops"])
    node(0.695, 0.055, 0.27, 0.028,
         "IN REPO: Docker · Compose · Actions · Pages\nRender · AWS SAM",
         "#eafaf1", fs=4.5, bold=True, ec="#1e8449")
    node(0.695, 0.030, 0.27, 0.022,
         "FUTURE: Daytona · Kubernetes · Kind · Jenkins",
         "#fdedec", fs=4.5, ec="#7b241c", ls="--")

    # Visual key for the connector semantics.
    ax.plot([0.025, 0.065], [0.015, 0.015], color=EDGE, lw=0.9)
    ax.text(0.072, 0.015, "solid = implemented path", va="center", fontsize=5.2, color=INK)
    ax.plot([0.275, 0.315], [0.015, 0.015], color="#7d3c98", lw=0.9, linestyle="--")
    ax.text(0.322, 0.015, "dashed purple = external observation", va="center", fontsize=5.2, color=INK)

    fig.savefig(OUT / "fig_architecture.pdf")
    plt.close(fig)


def fig_ecology():
    fig, ax = canvas(3.45, 2.5)
    agents = ["noise", "retail\n(herding)", "momentum", "mean\nreversion",
              "market\nmaker"]
    for i, name in enumerate(agents):
        box(ax, 0.02 + i * 0.196, 0.80, 0.17, 0.14, name, FILL["ui"], fs=6)
        arrow(ax, 0.105 + i * 0.196, 0.80, 0.42 + i * 0.03, 0.64)
    box(ax, 0.28, 0.50, 0.44, 0.14,
        "central limit order book\nprice–time priority · partial fills",
        FILL["core"], fs=6.2, bold=True)
    box(ax, 0.02, 0.50, 0.22, 0.14, "shock engine\nwhale · MM pull ·\nsentiment",
        FILL["warn"], fs=5.6)
    arrow(ax, 0.24, 0.57, 0.28, 0.57)
    box(ax, 0.76, 0.50, 0.22, 0.14, "regime engine\nstable / crisis /\nrecovery",
        FILL["research"], fs=5.6)
    arrow(ax, 0.76, 0.57, 0.72, 0.57)
    box(ax, 0.06, 0.26, 0.40, 0.13,
        "endogenous price, spread, depth\n(no external price series)",
        "#ffffff", fs=6)
    box(ax, 0.54, 0.26, 0.40, 0.13,
        "portfolio + margin accounting\nreservations · forced liquidation",
        "#ffffff", fs=6)
    arrow(ax, 0.40, 0.50, 0.28, 0.39)
    arrow(ax, 0.60, 0.50, 0.72, 0.39)
    box(ax, 0.20, 0.03, 0.60, 0.13,
        "agent memory: fear · confidence · trend · value\n(feedback into next-step behavior)", FILL["ext"], fs=6)
    arrow(ax, 0.30, 0.26, 0.42, 0.16)
    arrow(ax, 0.58, 0.16, 0.58, 0.26, style="<|-")
    fig.savefig(OUT / "fig_ecology.pdf")
    plt.close(fig)


def _pipeline(filename, stages, w=3.45, fs=6.0, fill="#ffffff"):
    n = len(stages)
    fig, ax = canvas(w, 0.52 * n)
    for i, text in enumerate(stages):
        y = 1 - (i + 1) / (n + 0.15)
        box(ax, 0.08, y, 0.84, 0.75 / (n + 0.15), text, fill, fs=fs)
        if i:
            y_prev = 1 - i / (n + 0.15)
            arrow(ax, 0.5, y_prev, 0.5, y + 0.75 / (n + 0.15))
    fig.savefig(OUT / filename)
    plt.close(fig)


def fig_repro():
    _pipeline("fig_repro_pipeline.pdf", [
        "canonical config (frozen pydantic, canonical JSON, schema version)",
        "ExperimentVersion — research hash =\nSHA-256(config ‖ design ‖ seed plan ‖ versions)",
        "deterministic seed allocation (cell × replication)",
        "execution → state hash · event hash · hash-chained event log",
        "artifact manifests (checksummed, content-addressed)",
        "replay / reproduce: re-execute and compare every stored hash",
    ], fill=FILL["research"])


def fig_workflow():
    _pipeline("fig_workflow.pdf", [
        "scenario draft (dashboard builder, mutable)",
        "validation → immutable ExperimentVersion (expv_…)",
        "BatchRunner / TradeOps: replicated cells (resumable)",
        "analysis: bootstrap CIs · permutation tests · Holm",
        "artifact-only report",
        "tezcat reproduce — hash-for-hash verification",
    ], fill=FILL["ui"])


def fig_s3():
    _pipeline("fig_s3_pipeline.pdf", [
        "Kalshi / Polymarket public endpoints (read-only)",
        "strict adapters: schema-drift detection · rate limits",
        "immutable external dataset (raw + normalized, SHA-256, terms)",
        "event signature (versioned, hashed observation window)",
        "candidate mechanisms (rule-based hypotheses)",
        "ordinary ExperimentVersion (dataset identity in research hash)",
        "synthetic experiment → analysis → reproduction",
    ], fill=FILL["ext"])


def fig_s4():
    _pipeline("fig_s4_bridge.pdf", [
        "registered experiment (cell, replication, seed)",
        "deterministic Market World (mw_…) + ecology fingerprint",
        "canonical stream → QuoteTick / TradeTick (validated)",
        "NautilusTrader backtest — strategy is an observer;\nno feedback into the ecology",
        "metrics from primary records + regime attribution",
        "result artifact: research → world → strategy hash chain",
    ], fill=FILL["core"])


def fig_devops():
    fig, ax = canvas(3.45, 2.1)
    impl = [("Dockerfile /\nCompose", 0.02), ("GitHub Actions\nCI + Pages", 0.27),
            ("Render\n(backend demo)", 0.52), ("AWS SAM\ntemplate", 0.77)]
    ax.text(0.5, 0.95, "implemented / configured in-repo", ha="center",
            fontsize=6.6, fontweight="bold", color=INK)
    for text, x in impl:
        box(ax, x, 0.62, 0.21, 0.22, text, FILL["research"], fs=5.8)
    ax.text(0.5, 0.52, "documented / demonstrated", ha="center", fontsize=6.6,
            fontweight="bold", color=INK)
    box(ax, 0.14, 0.28, 0.32, 0.16, "Daytona container\ndevelopment environment",
        FILL["ui"], fs=5.8)
    box(ax, 0.54, 0.28, 0.32, 0.16, "local pip / uvicorn\ndeployment",
        FILL["ui"], fs=5.8)
    ax.text(0.5, 0.20, "future work (no in-repo evidence)", ha="center",
            fontsize=6.6, fontweight="bold", color="#7b241c")
    box(ax, 0.14, 0.015, 0.32, 0.13, "Kubernetes /\nMinikube / Kind",
        FILL["warn"], fs=5.8)
    box(ax, 0.54, 0.015, 0.32, 0.13, "Jenkins pipelines", FILL["warn"], fs=5.8)
    fig.savefig(OUT / "fig_devops.pdf")
    plt.close(fig)


if __name__ == "__main__":
    fig_architecture()
    fig_ecology()
    fig_repro()
    fig_workflow()
    fig_s3()
    fig_s4()
    fig_devops()
    print("done:", sorted(p.name for p in OUT.glob("fig_*.pdf")))

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
    fig, ax = canvas(7.0, 4.6)
    # Row 1: user + UI + control plane
    box(ax, 0.01, 0.90, 0.14, 0.075, "Researcher\n(CLI / dashboard)",
        FILL["ui"], bold=True)
    box(ax, 0.19, 0.90, 0.20, 0.075, "React/Vite dashboard\nBUILD·RESEARCH·LAB·MARKETS·TRADEOPS", FILL["ui"])
    box(ax, 0.43, 0.90, 0.16, 0.075, "FastAPI control plane\n/api/*", FILL["ui"])
    box(ax, 0.63, 0.90, 0.355, 0.075,
        "Experiment registry · Run manager · Research API · TradeOps queue",
        FILL["ui"])
    arrow(ax, 0.15, 0.9375, 0.19, 0.9375)
    arrow(ax, 0.39, 0.9375, 0.43, 0.9375)
    arrow(ax, 0.59, 0.9375, 0.63, 0.9375)

    # Row 2: ecology engine
    box(ax, 0.01, 0.60, 0.47, 0.25, "", FILL["core"])
    ax.text(0.245, 0.825, "MARKET ECOLOGY ENGINE (deterministic step loop)",
            ha="center", fontsize=6.8, fontweight="bold", color=INK)
    inner = [("Shock\nengine", 0.025), ("Regime\nengine", 0.105),
             ("Agent ecology\n5 strategy types", 0.185),
             ("Agent\nmemory", 0.30), ("CLOB +\nmatching", 0.38)]
    for text, x in inner:
        box(ax, x, 0.71, 0.072 if "Agent ecology" not in text else 0.108,
            0.075, text, "#ffffff", fs=5.6)
    inner2 = [("Portfolio /\nmargin", 0.025), ("Risk &\nliquidation", 0.105),
              ("Event log\n(hash chain)", 0.185), ("Metrics\nengine", 0.30),
              ("Checkpoints\n& forks", 0.38)]
    for text, x in inner2:
        box(ax, x, 0.62, 0.072 if "Event" not in text else 0.108, 0.075,
            text, "#ffffff", fs=5.6)
    arrow(ax, 0.245, 0.90, 0.245, 0.85)

    # Row 2b: research layer
    box(ax, 0.52, 0.60, 0.465, 0.25, "", FILL["research"])
    ax.text(0.7525, 0.825, "RESEARCH LAYER", ha="center", fontsize=6.8,
            fontweight="bold", color=INK)
    for text, x, y in [("BatchRunner\n(serial ≡ parallel)", 0.535, 0.71),
                       ("Analysis\nbootstrap · permutation", 0.655, 0.71),
                       ("Stylized facts\n& calibration", 0.785, 0.71),
                       ("Replay &\nreproduction", 0.895, 0.71),
                       ("Artifact-only\nreports", 0.535, 0.62),
                       ("State/event\nhashes", 0.655, 0.62),
                       ("Scenario\nbuilder", 0.785, 0.62),
                       ("Research\nartifact graph", 0.895, 0.62)]:
        box(ax, x, y, 0.105 if x < 0.89 else 0.088, 0.075, text, "#ffffff",
            fs=5.6)
    arrow(ax, 0.48, 0.725, 0.52, 0.725)

    # Row 3: external + worlds + nautilus
    box(ax, 0.01, 0.30, 0.30, 0.22, "", FILL["ext"])
    ax.text(0.16, 0.49, "EXTERNAL EVENT-MARKET\nINTELLIGENCE (read-only)",
            ha="center", fontsize=6.4, fontweight="bold", color=INK)
    box(ax, 0.025, 0.395, 0.125, 0.06, "Kalshi adapter", "#ffffff", fs=5.6)
    box(ax, 0.165, 0.395, 0.13, 0.06, "Polymarket adapter", "#ffffff", fs=5.6)
    box(ax, 0.025, 0.315, 0.27, 0.06,
        "Immutable datasets · event signatures · divergence", "#ffffff",
        fs=5.6)

    box(ax, 0.35, 0.30, 0.28, 0.22, "", FILL["core"])
    ax.text(0.49, 0.49, "SYNTHETIC MARKET WORLDS", ha="center", fontsize=6.4,
            fontweight="bold", color=INK)
    box(ax, 0.365, 0.395, 0.25, 0.06,
        "deterministic realization · world hash", "#ffffff", fs=5.6)
    box(ax, 0.365, 0.315, 0.25, 0.06,
        "ecology fingerprint · quote/trade stream", "#ffffff", fs=5.6)

    box(ax, 0.67, 0.30, 0.32, 0.22, "", FILL["research"])
    ax.text(0.83, 0.49, "NAUTILUSTRADER BRIDGE (backtest only)",
            ha="center", fontsize=6.4, fontweight="bold", color=INK)
    box(ax, 0.685, 0.395, 0.135, 0.06, "QuoteTick/TradeTick\nexport",
        "#ffffff", fs=5.4)
    box(ax, 0.835, 0.395, 0.14, 0.06, "hashed strategy library",
        "#ffffff", fs=5.4)
    box(ax, 0.685, 0.315, 0.29, 0.06,
        "PnL · drawdown · slippage · fills · regime-conditioned metrics",
        "#ffffff", fs=5.4)

    arrow(ax, 0.16, 0.60, 0.16, 0.52)
    arrow(ax, 0.31, 0.41, 0.35, 0.41)
    arrow(ax, 0.49, 0.60, 0.49, 0.52)
    arrow(ax, 0.63, 0.41, 0.67, 0.41)

    # Row 4: plane + devops
    box(ax, 0.01, 0.13, 0.62, 0.12, "", FILL["research"])
    ax.text(0.32, 0.225, "RESEARCH CONTROL PLANE (S5)", ha="center",
            fontsize=6.4, fontweight="bold", color=INK)
    box(ax, 0.025, 0.145, 0.28, 0.055,
        "artifact graph: dataset → forecast → portfolio →\nworld → backtest → risk → report", "#ffffff", fs=5.4)
    box(ax, 0.325, 0.145, 0.14, 0.055, "forecast desk\n(reference model)",
        "#ffffff", fs=5.4)
    box(ax, 0.48, 0.145, 0.135, 0.055, "portfolio desk\n(ref. CVaR / skfolio)",
        "#ffffff", fs=5.4)
    arrow(ax, 0.32, 0.30, 0.32, 0.25)

    box(ax, 0.67, 0.13, 0.32, 0.12, "", FILL["ops"])
    ax.text(0.83, 0.225, "DEVOPS / DEPLOYMENT", ha="center", fontsize=6.4,
            fontweight="bold", color=INK)
    box(ax, 0.685, 0.145, 0.29, 0.055,
        "Docker · Compose · GitHub Actions · Pages ·\nRender · AWS SAM · Daytona (impl.)  |  K8s, Jenkins (future)",
        "#ffffff", fs=5.2)

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

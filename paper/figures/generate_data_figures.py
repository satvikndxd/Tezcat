#!/usr/bin/env python3
"""Generate the data-driven figures for the Tezcat IEEE paper.

Every plot is produced from actual repository executions (frozen seed-42
preset runs, the committed leverage×liquidity experiment specs, a Strategy
Lab backtest) — nothing is fabricated. Run from the repository root:

    .venv/bin/python paper/figures/generate_data_figures.py <workdir>

<workdir> must contain the re-executed experiments (see paper/README.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
OUT = Path(__file__).resolve().parent

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
    "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "figure.dpi": 150, "savefig.bbox": "tight",
    "axes.spines.top": False, "axes.spines.right": False,
})
C = {"stable": "#3a7ca5", "flash": "#c0392b", "bubble": "#8e6bbf",
    "syn": "#3a7ca5", "obs": "#c0392b"}


def run_preset(preset_id: str, seed: int = 42):
    from tezcat.engine.ecology import EcologyEngine
    from tezcat.experiments.presets import build_config
    cfg = build_config(preset_id, {"step_delay_ms": 0})
    eng = EcologyEngine(f"paper_{preset_id}", cfg, seed)
    while not eng.done:
        eng.step()
    return eng


def fig_presets(engines):
    fig, ax = plt.subplots(figsize=(3.45, 1.9))
    for (label, key), eng in engines.items():
        prices = [s["last_price"] for s in eng.snapshots]
        ax.plot(range(len(prices)), prices, lw=0.9, color=C[key], label=label)
    ax.set_xlabel("simulation step")
    ax.set_ylabel("last trade price")
    ax.legend(frameon=False, ncol=3, loc="upper left")
    fig.savefig(OUT / "fig_presets.pdf")
    plt.close(fig)


def fig_flash_anatomy(eng):
    snaps = eng.snapshots
    steps = [s["step"] for s in snaps]
    fig, axes = plt.subplots(3, 1, figsize=(3.45, 3.4), sharex=True)
    axes[0].plot(steps, [s["last_price"] for s in snaps], lw=0.8,
                 color=C["flash"])
    axes[0].set_ylabel("price")
    axes[1].plot(steps, [s["spread"] for s in snaps], lw=0.6, color="#7d6608")
    axes[1].set_ylabel("spread")
    axes[2].plot(steps, [s["bid_depth"] + s["ask_depth"] for s in snaps],
                 lw=0.6, color="#1e8449")
    axes[2].set_ylabel("book depth")
    axes[2].set_xlabel("simulation step")
    # regime shading
    for ax in axes:
        current, start = "stable", 0
        for ev in eng.regimes.events:
            if current == "crisis":
                ax.axvspan(start, ev.step, color="#c0392b", alpha=0.10, lw=0)
            elif current == "recovery":
                ax.axvspan(start, ev.step, color="#1e8449", alpha=0.08, lw=0)
            current, start = ev.new_regime, ev.step
        end = steps[-1]
        if current == "crisis":
            ax.axvspan(start, end, color="#c0392b", alpha=0.10, lw=0)
        elif current == "recovery":
            ax.axvspan(start, end, color="#1e8449", alpha=0.08, lw=0)
    fig.savefig(OUT / "fig_flash_anatomy.pdf")
    plt.close(fig)


def fig_agent_pnl(eng):
    report = eng.build_report()
    by_type = report["agent_pnl_by_type"]  # artifact-backed, as in smoke.py
    names = sorted(by_type)
    values = [by_type[n]["total_pnl"] for n in names]
    fig, ax = plt.subplots(figsize=(3.45, 1.8))
    colors = ["#1e8449" if v >= 0 else "#c0392b" for v in values]
    ax.barh([n.replace("_trader", "").replace("_", " ") for n in names],
            values, color=colors, height=0.6)
    ax.axvline(0, color="black", lw=0.6)
    ax.set_xlabel("aggregate PnL (flash-crash preset, seed 42)")
    fig.savefig(OUT / "fig_agent_pnl.pdf")
    plt.close(fig)


def fig_stylized(stable_cfg_engines, target_prices):
    from tezcat.analysis.stylized_facts import compare_features, extract_features
    ensemble = [extract_features([s["last_price"] for s in e.snapshots])
                for e in stable_cfg_engines]
    target = extract_features(target_prices)
    comparison = compare_features(target, ensemble)
    feats = ["return_std", "return_excess_kurtosis", "hill_tail_index",
             "acf_returns_lag1", "acf_abs_returns_lag1",
             "volatility_clustering", "max_drawdown"]
    rows = [(f, comparison["features"][f]) for f in feats
            if f in comparison["features"] and "warning" not in
            comparison["features"][f]]
    fig, ax = plt.subplots(figsize=(3.45, 2.2))
    for i, (name, row) in enumerate(rows):
        lo, hi, mean = row["band_q05"], row["band_q95"], row["ensemble_mean"]
        scale = max(abs(lo), abs(hi), abs(row["real"]), 1e-9)
        ax.plot([lo / scale, hi / scale], [i, i], color=C["syn"], lw=4,
                alpha=0.35, solid_capstyle="butt")
        ax.plot(mean / scale, i, "o", color=C["syn"], ms=3.5)
        ax.plot(row["real"] / scale, i, "D", color=C["obs"], ms=3.5)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0].replace("_", " ") for r in rows])
    ax.set_xlabel("feature value (per-feature normalized)")
    ax.plot([], [], "o", color=C["syn"], label="stable ensemble (q05–q95)")
    ax.plot([], [], "D", color=C["obs"], label="flash-crash target (synthetic)")
    ax.legend(frameon=False, loc="lower right")
    fig.savefig(OUT / "fig_stylized.pdf")
    plt.close(fig)


def load_map_cells(workdir: Path):
    """Per-cell mean drawdown + crash frequency from actual result rows."""
    from tezcat.experiments.batch import batch_id_for
    from tezcat.experiments.registry import Registry
    registry = Registry(str(workdir / "data"))
    version = registry.load("expv_60fabaa6f7ca")
    rows = registry.list_result_rows(batch_id_for(version))
    cells = {}
    for row in rows:
        cells.setdefault(row["cell"], []).append(row["metrics"])
    return cells


def fig_levliq(cells):
    ims = ["0.5", "0.25", "0.1667", "0.125", "0.1"]
    lev_labels = ["2x", "4x", "6x", "8x", "10x"]
    mms = ["1", "3", "5"]
    crash = [[0.0] * len(mms) for _ in ims]
    dd = {mm: [] for mm in mms}
    for i, im in enumerate(ims):
        for j, mm in enumerate(mms):
            metrics = cells[f"im={im}|mm={mm}"]
            crash[i][j] = 100.0 * sum(m["crash_detected"] for m in metrics) \
                / len(metrics)
            dd[mm].append(sum(m["max_drawdown"] for m in metrics) / len(metrics))
    # heatmap
    fig, ax = plt.subplots(figsize=(2.6, 2.4))
    im_ = ax.imshow(crash, cmap="Reds", vmin=0, vmax=70, aspect="auto")
    ax.set_xticks(range(len(mms)))
    ax.set_xticklabels([f"{m} MM" for m in mms])
    ax.set_yticks(range(len(ims)))
    ax.set_yticklabels(lev_labels)
    ax.set_xlabel("market-maker count (liquidity)")
    ax.set_ylabel("leverage cap")
    for i in range(len(ims)):
        for j in range(len(mms)):
            ax.text(j, i, f"{crash[i][j]:.0f}%", ha="center", va="center",
                    fontsize=7,
                    color="white" if crash[i][j] > 40 else "black")
    fig.colorbar(im_, ax=ax, shrink=0.85, label="crash frequency (%)")
    fig.savefig(OUT / "fig_levliq_heatmap.pdf")
    plt.close(fig)
    # drawdown curves
    fig, ax = plt.subplots(figsize=(3.45, 2.0))
    x = [2, 4, 6, 8, 10]
    styles = {"1": ("#c0392b", "o"), "3": ("#7d6608", "s"),
              "5": ("#1e8449", "^")}
    for mm in mms:
        color, marker = styles[mm]
        ax.plot(x, dd[mm], marker=marker, ms=3.5, lw=1.0, color=color,
                label=f"{mm} market maker{'s' if mm != '1' else ''}")
    ax.set_xlabel("leverage cap")
    ax.set_ylabel("mean max drawdown")
    ax.set_xticks(x)
    ax.legend(frameon=False)
    fig.savefig(OUT / "fig_leverage_dd.pdf")
    plt.close(fig)


def fig_regime_strategy(workdir: Path):
    """Flash-crash world → EMA-cross backtest → regime-conditioned PnL."""
    from tezcat.core.config import ExperimentConfig
    from tezcat.experiments.presets import build_config
    from tezcat.experiments.registry import Registry
    from tezcat.experiments.schema import Arm, DesignSpec, ExperimentVersion
    from tezcat.lab.backtest import run_backtest
    from tezcat.worlds import build_world

    registry = Registry(str(workdir / "data"))
    cfg = build_config("flash_crash", {"step_delay_ms": 0})
    design = DesignSpec(design_type="baseline",
                        question="paper figure: flash-crash world?",
                        hypothesis="demonstration world for the paper",
                        dependent_variables=["max_drawdown"],
                        primary_metric="max_drawdown", replications=1,
                        control=Arm(name="flash"))
    vid = registry.register(ExperimentVersion("exp_paper_flash",
                                              "paper-flash", cfg, design))
    world = build_world(registry, vid)
    result = run_backtest(world, "ema_cross", {"trade_size": 100})
    breakdown = result["metrics"]["regime_breakdown"]
    regimes = [r for r in ("stable", "crisis", "recovery") if r in breakdown]
    fig, axes = plt.subplots(1, 2, figsize=(3.45, 1.7))
    pnl = [breakdown[r]["pnl"] for r in regimes]
    slip = [breakdown[r]["mean_slippage_vs_mid"] or 0 for r in regimes]
    colors = {"stable": "#3a7ca5", "crisis": "#c0392b",
              "recovery": "#1e8449"}
    axes[0].bar(regimes, pnl, color=[colors[r] for r in regimes], width=0.6)
    axes[0].axhline(0, color="black", lw=0.6)
    axes[0].set_ylabel("PnL")
    axes[0].tick_params(axis="x", rotation=20)
    axes[1].bar(regimes, slip, color=[colors[r] for r in regimes], width=0.6)
    axes[1].set_ylabel("mean slippage vs mid")
    axes[1].tick_params(axis="x", rotation=20)
    fig.suptitle("EMA-cross under a flash-crash world, by regime", y=1.04)
    fig.savefig(OUT / "fig_regime_strategy.pdf")
    plt.close(fig)
    return result


def main():
    workdir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/paperdata")
    print("running seed-42 presets…")
    stable = run_preset("stable_baseline")
    flash = run_preset("flash_crash")
    bubble = run_preset("bubble_formation")
    fig_presets({("stable baseline", "stable"): stable,
                 ("flash crash", "flash"): flash,
                 ("bubble formation", "bubble"): bubble})
    fig_flash_anatomy(flash)
    fig_agent_pnl(flash)
    print("running stylized-facts ensemble…")
    from tezcat.core.seeds import derive_seed
    from tezcat.engine.ecology import EcologyEngine
    from tezcat.experiments.presets import build_config
    cfg = build_config("stable_baseline", {"step_delay_ms": 0})
    ensemble = []
    for i in range(12):
        seed = derive_seed(42, "paper", "stylized", i)
        e = EcologyEngine(f"paper_ens_{i}", cfg, seed)
        while not e.done:
            e.step()
        ensemble.append(e)
    fig_stylized(ensemble, [s["last_price"] for s in flash.snapshots])
    print("loading leverage×liquidity result rows…")
    fig_levliq(load_map_cells(workdir))
    print("running strategy-lab regime figure…")
    fig_regime_strategy(workdir)
    print("done:", sorted(p.name for p in OUT.glob("fig_*.pdf")))


if __name__ == "__main__":
    main()

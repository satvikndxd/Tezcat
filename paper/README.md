# Tezcat IEEE Paper

`main.tex` — "Tezcat: A Deterministic, Provenance-Aware Laboratory for
Experimental Research on Emergent Financial-Market Phenomena"
(IEEEtran conference format). Compiled output: `tezcat_ieee.pdf`.

## Provenance

- **Repository commit used for every quantitative claim:**
  `16448b45c1c4ca2da92ff0ce2d68388d0d182492`
- **Test suite at that commit:** 415 passed (verified by running
  `.venv/bin/python -m pytest tests -q`).
- All experiment statistics were **re-executed** at that commit (not
  copied from documentation): the leverage×liquidity map
  (`expv_60fabaa6f7ca`, 300 runs), the 2×2 extremes
  (`expv_cfc17774801a`, 120 runs), and the margin-spiral A/B
  (`expv_417305c181af`, 40 runs) all re-minted their archived research
  hashes. Frozen seed-42 preset values come from
  `docs/audit/F0_baseline.md` and are re-verified by `scripts/smoke.py`.

## Dependencies

- TeX Live with `IEEEtran` (Debian: `texlive-latex-base`,
  `texlive-latex-extra`, `texlive-publishers`, `texlive-bibtex-extra`)
- Python environment of the repository (`pip install -e ".[dev,lab,plane]"`)
  plus `matplotlib` for figure generation

## Build

```bash
# one-shot compile (uses the committed figures)
./build.sh                       # → tezcat_ieee.pdf

# full regeneration, including figures from repository data
mkdir -p /tmp/paperdata && cd /tmp/paperdata
tezcat --data-dir data run <repo>/examples/leverage_liquidity_map.json --workers 4
tezcat --data-dir data run <repo>/examples/leverage_liquidity_2x2.json --workers 4
tezcat --data-dir data run <repo>/examples/margin_spiral_ab.json
cd <repo>/paper
REGEN_FIGURES=1 TEZCAT_PAPER_WORKDIR=/tmp/paperdata ./build.sh
```

## Figure scripts

- `figures/generate_diagrams.py` — architecture/pipeline diagrams
  (vector PDF, no data dependencies)
- `figures/generate_data_figures.py <workdir>` — all data plots,
  generated from actual seed-42 preset runs, the re-executed
  leverage×liquidity result rows, a 12-replication stylized-facts
  ensemble, and a Strategy Lab backtest. Nothing is fabricated.

## Reproducing the paper's experiments

```bash
tezcat run examples/leverage_liquidity_map.json
tezcat analyze expv_60fabaa6f7ca
tezcat report  expv_60fabaa6f7ca -o report.md
tezcat reproduce 60fabaa6        # verifies every stored hash
```

## Notes

- Tables are defined inline in `main.tex` (Tables I–VIII); there are no
  external table files.
- The author-affiliation field is a deliberate placeholder: no
  institutional affiliation is established in repository materials.

#!/usr/bin/env bash
# Build the Tezcat IEEE paper. Run from the repository root or paper/.
set -euo pipefail
cd "$(dirname "$0")"

# 1. (Optional) regenerate figures from repository data.
#    Requires a workdir with the re-executed experiments (see README.md).
if [ "${REGEN_FIGURES:-0}" = "1" ]; then
  WORKDIR="${TEZCAT_PAPER_WORKDIR:-/tmp/paperdata}"
  ../.venv/bin/python figures/generate_diagrams.py
  ../.venv/bin/python figures/generate_data_figures.py "$WORKDIR"
fi

# 2. Compile (pdflatex + bibtex, standard IEEE cycle).
pdflatex -interaction=nonstopmode -halt-on-error main.tex > /dev/null
bibtex main > /dev/null
pdflatex -interaction=nonstopmode -halt-on-error main.tex > /dev/null
pdflatex -interaction=nonstopmode -halt-on-error main.tex > /dev/null

cp main.pdf tezcat_ieee.pdf
echo "OK: paper/tezcat_ieee.pdf"
grep -c "Warning" main.log || true

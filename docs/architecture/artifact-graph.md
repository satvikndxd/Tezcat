# The Research Artifact Graph (S5-A)

One provenance system for the whole quantitative workflow.

## Node contract

```python
ResearchArtifact(
    artifact_id,         # <type-prefix>_<sha256[:12]>
    artifact_type,       # external_dataset | forecast | portfolio |
                         # market_world | backtest | risk_report | … 
    artifact_hash,       # content identity (see below)
    parent_hashes,       # provenance edges — parents must already exist
    external_identity,   # wrapped native Tezcat identity, verbatim
    schema_version, code_version,
    created_at, environment,   # provenance, not identity
    config, payload_checksum, metadata,
)
```

## Identity rules

* `artifact_hash = sha256(schema version, type, config, payload checksum,
  parent hashes, external identity, code version)` — content-addressed,
  idempotent registration, collision-safe.
* **Environment is provenance, not identity**: python/platform and
  optional-package versions (nautilus_trader, skfolio, numpy) are
  recorded on every node and *checked loudly at reproduction*, but do
  not change the hash — "same content, same identity" holds across
  machines while dependency drift is still detected, never absorbed.
* **No second provenance system**: research hashes (`expv_`), dataset
  hashes (`exd_`), world hashes (`mw_`), and lab result ids (`lab_`)
  are carried verbatim as `external_identity` and inside payloads. Graph
  prefixes (`fct_`, `pft_`, `wld_`, `btr_`, `rsk_`, `rpt_`, …) are
  distinct from every native prefix so wrapping is never mistaken for
  re-minting.

## Integrity

* Write-once: re-registering identical content returns the same node;
  a colliding id with different content raises.
* Payloads are checksummed and re-verified on every load; tampered
  artifacts refuse to load.
* Registering a child whose parent hash is unknown to the graph is a
  schema violation ("register parents before children"), so dangling
  provenance cannot exist.
* `tezcat plane verify <id>` re-verifies checksum + hash from disk;
  `tezcat plane show <id>` prints the root-first lineage.

## Traversal

`lineage(id)` returns the deduplicated ancestor closure root-first
(diamonds collapse); `children(id)` lists downstream nodes. The graph is
append-only — history is never rewritten.

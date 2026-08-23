# External-provider fixtures

Every fixture bundle in this tree declares a `fixture_label`:

- **`SYNTHETIC FIXTURE`** — invented data, shaped for tests. It is *not*
  provider data and must never be presented as such.
- **`RECORDED PROVIDER RESPONSE`** — a verbatim recorded API response.
  Requires a documented `provenance` (when/how recorded, applicable terms).

The two are never blurred: `tezcat.external.fixtures.load_fixture` rejects
unlabeled files, and a `RECORDED` bundle without provenance fails to load.

The current bundles are all **synthetic**: no recorded provider responses
are stored in this repository, so no provider terms questions arise. They
model one information-shock episode (probability ~0.42 → ~0.71 with a
volume surge and spread expansion) mirrored across a fictional Kalshi
market (`SYN-MKT-YES`) and a fictional Polymarket market (`912001`,
offset ~+1.5 pp so cross-provider divergence tests have signal).

Failure-injection payloads (`__status__`, `__timeout__`, `__malformed__`,
`__sequence__`) are documented in `tezcat/external/fixtures.py`; the
failure-matrix tests build those routes inline.

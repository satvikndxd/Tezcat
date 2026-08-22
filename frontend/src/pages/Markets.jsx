import React, { useEffect, useState } from 'react';
import { api, asArray, fmtDate, fmtNum, truncHash } from '../api/client';
import { EmptyRow, Panel, StatusBadge } from '../components/ui.jsx';

function ProviderRow({ provider, selected, onClick }) {
  return (
    <button className={`btn ${selected ? '' : 'ghost'}`} onClick={() => onClick(provider.provider_id)}>
      {provider.provider_id.toUpperCase()}
    </button>
  );
}

function Orderbook({ book }) {
  if (!book) return <div className="msg">NO ORDER BOOK</div>;
  const bids = asArray(book.bids);
  const asks = asArray(book.asks);
  return (
    <div className="grid cols-2">
      <div>
        <div className="label">BIDS — BEST FIRST</div>
        <table className="t"><thead><tr><th className="l">PRICE</th><th>SIZE</th></tr></thead><tbody>
          {bids.slice(0, 10).map((row, i) => <tr key={`bid-${i}`}><td className="pos l">{fmtNum(row.price, 4)}</td><td>{fmtNum(row.quantity, 2)}</td></tr>)}
          {!bids.length && <EmptyRow cols={2} text="NO BIDS" />}
        </tbody></table>
      </div>
      <div>
        <div className="label">ASKS — BEST FIRST</div>
        <table className="t"><thead><tr><th className="l">PRICE</th><th>SIZE</th></tr></thead><tbody>
          {asks.slice(0, 10).map((row, i) => <tr key={`ask-${i}`}><td className="neg l">{fmtNum(row.price, 4)}</td><td>{fmtNum(row.quantity, 2)}</td></tr>)}
          {!asks.length && <EmptyRow cols={2} text="NO ASKS" />}
        </tbody></table>
      </div>
    </div>
  );
}

function MarketRow({ market, onClick, selected }) {
  const yes = market.outcomes?.find((outcome) => String(outcome.outcome_id).toLowerCase() === 'yes');
  return (
    <tr className={`clickable ${selected ? 'selected-row' : ''}`} onClick={() => onClick(market)}>
      <td className="accent">{market.market_id}</td>
      <td className="l">{market.title}</td>
      <td><StatusBadge status={market.status} /></td>
      <td>{yes?.token_id ? truncHash(yes.token_id, 16) : '—'}</td>
    </tr>
  );
}

function Detail({ provider, market, onClose, onDatasetRegistered }) {
  const [detail, setDetail] = useState(null);
  const [book, setBook] = useState(null);
  const [history, setHistory] = useState(null);
  const [err, setErr] = useState(null);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [snapshotMsg, setSnapshotMsg] = useState(null);
  const tokenId = market?.outcomes?.find((outcome) => String(outcome.outcome_id).toLowerCase() === 'yes')?.token_id;

  useEffect(() => {
    if (!market) return undefined;
    setErr(null); setDetail(null); setBook(null); setHistory(null); setSnapshotMsg(null);
    api.externalMarket(provider, market.market_id).then((value) => setDetail(value.market)).catch((e) => setErr(e.message));
    api.externalOrderbook(provider, market.market_id, tokenId || '').then((value) => setBook(value.orderbook)).catch((e) => setErr(e.message));
    if (provider === 'polymarket' && tokenId) {
      api.externalHistory(provider, tokenId, '&interval=1h').then((value) => setHistory(value.items)).catch(() => {});
    }
    return undefined;
  }, [provider, market?.market_id, tokenId]);

  if (!market) return null;
  const registerSnapshot = async () => {
    setSnapshotBusy(true); setSnapshotMsg(null); setErr(null);
    try {
      const result = await api.externalSnapshot(provider, market.market_id, { source_id: market.market_id });
      setSnapshotMsg(`registered ${result.manifest.dataset_id}`);
      if (onDatasetRegistered) onDatasetRegistered(result.manifest);
    } catch (e) {
      setErr(e.message);
    } finally {
      setSnapshotBusy(false);
    }
  };

  return (
    <Panel title="OBSERVED MARKET DETAIL" sub={`${provider.toUpperCase()} · read-only`}>
      <div className="toolbar">
        <button className="btn ghost" onClick={onClose}>CLOSE</button>
        <button className="btn" disabled={snapshotBusy} onClick={registerSnapshot}>{snapshotBusy ? 'REGISTERING…' : 'REGISTER OBSERVED SNAPSHOT'}</button>
        <span className="dim small">Manual, bounded capture only. No orders, wallets, or execution controls.</span>
      </div>
      {err && <div className="err section-gap">{err}</div>}
      {snapshotMsg && <div className="msg section-gap">{snapshotMsg} · raw and normalized artifacts retained.</div>}
      <div className="kv section-gap">
        <span className="k">MARKET</span><span className="v accent">{market.market_id}</span>
        <span className="k">QUESTION</span><span className="v">{detail?.title || market.title}</span>
        <span className="k">STATUS</span><span className="v"><StatusBadge status={detail?.status || market.status} /></span>
        <span className="k">OUTCOMES</span><span className="v">{asArray(market.outcomes).map((outcome) => `${outcome.label}${outcome.token_id ? `:${truncHash(outcome.token_id, 12)}` : ''}`).join(' · ')}</span>
      </div>
      <div className="section-gap"><Orderbook book={book} /></div>
      {history && (
        <div className="section-gap">
          <div className="label">PUBLIC PRICE HISTORY — {history.length} OBSERVATIONS</div>
          <table className="t"><thead><tr><th className="l">TIME</th><th>IMPLIED PROBABILITY</th></tr></thead><tbody>
            {asArray(history).slice(-24).map((row, i) => <tr key={i}><td className="l">{fmtDate(row.timestamp)}</td><td className="val">{fmtNum(row.implied_probability, 4)}</td></tr>)}
          </tbody></table>
        </div>
      )}
    </Panel>
  );
}

function DatasetPanel({ provider, refreshToken }) {
  const [datasets, setDatasets] = useState(null);
  const [selected, setSelected] = useState(null);
  const [signature, setSignature] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [compiled, setCompiled] = useState(null);
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState(null);

  const loadDatasets = () => {
    setErr(null);
    api.externalDatasets(provider).then((value) => setDatasets(asArray(value.items))).catch((e) => setErr(e.message));
  };
  useEffect(() => {
    setSelected(null); setSignature(null); setProposal(null); setCompiled(null); setApproved(false);
    loadDatasets();
  }, [provider, refreshToken]);

  const selectDataset = async (row) => {
    setBusy('dataset'); setErr(null); setSignature(null); setProposal(null); setCompiled(null); setApproved(false);
    try { setSelected(await api.externalDataset(row.dataset_id)); } catch (e) { setErr(e.message); }
    finally { setBusy(''); }
  };
  const extractSignature = async () => {
    setBusy('signature'); setErr(null);
    try {
      const result = await api.externalSignature(selected.manifest.dataset_id, { window: selected.manifest.time_window || {} });
      setSignature(result.signature);
    } catch (e) { setErr(e.message); }
    finally { setBusy(''); }
  };
  const makeProposal = async () => {
    const marketId = selected?.manifest?.market_ids?.[0];
    setBusy('proposal'); setErr(null);
    try {
      setProposal(await api.externalProposal({
        dataset_id: selected.manifest.dataset_id,
        market_id: marketId || 'unknown',
        question: 'Which declared synthetic mechanism resembles the observed event?',
        hypothesis: 'A declared mechanism may reproduce selected observed signature features.',
      }));
    } catch (e) { setErr(e.message); }
    finally { setBusy(''); }
  };
  const compile = async () => {
    setBusy('compile'); setErr(null);
    try {
      const preset = await api.preset('stable_baseline');
      const marketId = selected.manifest.market_ids?.[0] || 'unknown';
      setCompiled(await api.externalCompile({
        ...proposal,
        approved: true,
        signature,
        base_config: preset.config_template,
        experiment_id: `exp_external_${selected.manifest.dataset_id}`,
        name: `Observed ${marketId} baseline`,
        replications: 2,
      }));
    } catch (e) { setErr(e.message); }
    finally { setBusy(''); }
  };

  return (
    <Panel title="EXTERNAL DATASETS" sub="immutable manifests · raw + normalized lineage">
      {err && <div className="err section-gap">{err}</div>}
      <div className="small dim">Datasets are captured only when a researcher presses the manual registration button. Signatures are <span className="accent">INFERRED</span>; compilation creates a normal synthetic version only after explicit approval.</div>
      <div className="section-gap">
        <table className="t"><thead><tr><th className="l">DATASET</th><th>RETRIEVED</th><th>MARKETS</th><th>NORMALIZED CHECKSUM</th></tr></thead><tbody>
          {datasets === null && <EmptyRow cols={4} text="LOADING DATASETS…" />}
          {datasets !== null && !datasets.length && <EmptyRow cols={4} text="NO REGISTERED OBSERVED DATASETS" />}
          {asArray(datasets).map((row) => <tr key={row.dataset_id} className={`clickable ${selected?.manifest?.dataset_id === row.dataset_id ? 'selected-row' : ''}`} onClick={() => selectDataset(row)}>
            <td className="accent l">{truncHash(row.dataset_id, 22)}</td><td>{fmtDate(row.retrieval_timestamp)}</td><td>{asArray(row.market_ids).join(', ') || '—'}</td><td>{truncHash(row.normalized_checksum, 18)}</td>
          </tr>)}
        </tbody></table>
      </div>
      {selected && (
        <div className="section-gap">
          <div className="label">SELECTED OBSERVED DATASET</div>
          <div className="kv">
            <span className="k">PROVIDER</span><span className="v">{selected.manifest.provider}</span>
            <span className="k">DATASET ID</span><span className="v accent">{selected.manifest.dataset_id}</span>
            <span className="k">RAW CHECKSUM</span><span className="v">{selected.manifest.raw_checksum}</span>
            <span className="k">NORMALIZED CHECKSUM</span><span className="v">{selected.manifest.normalized_checksum}</span>
            <span className="k">TERMS</span><span className="v">{selected.manifest.license_terms}</span>
          </div>
          <div className="toolbar section-gap">
            <button className="btn" disabled={busy !== ''} onClick={extractSignature}>{busy === 'signature' ? 'EXTRACTING…' : 'EXTRACT INFERRED SIGNATURE'}</button>
            {signature && <button className="btn ghost" disabled={busy !== ''} onClick={makeProposal}>{busy === 'proposal' ? 'DRAFTING…' : 'RESEARCH THIS EVENT'}</button>}
          </div>
          {signature && <div className="msg">INFERRED signature {signature.signature_id} · Δ probability {fmtNum(signature.delta_probability, 4)} · checksum {truncHash(signature.checksum, 20)}</div>}
          {proposal && (
            <div className="section-gap">
              <div className="label">HYPOTHESIS PROPOSAL — NOT APPROVED</div>
              <div className="small dim">{proposal.next_step}</div>
              <div className="kv section-gap"><span className="k">QUESTION</span><span className="v">{proposal.question}</span><span className="k">HYPOTHESIS</span><span className="v">{proposal.hypothesis}</span><span className="k">LANGUAGE</span><span className="v">{asArray(proposal.language).join(' · ')}</span></div>
              <label className="small"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /> I explicitly approve compiling this proposal into an ordinary ExperimentVersion.</label>
              <div className="toolbar section-gap"><button className="btn" disabled={!approved || busy !== ''} onClick={compile}>{busy === 'compile' ? 'COMPILING…' : 'COMPILE APPROVED RESEARCH VERSION'}</button></div>
            </div>
          )}
          {compiled && <div className="msg section-gap">MODEL_RESULT registered as {compiled.version_id}. It has not been run automatically; use the existing RESEARCH workflow for batch, analysis, report, and reproduction.</div>}
        </div>
      )}
    </Panel>
  );
}

export default function Markets() {
  const [providers, setProviders] = useState(null);
  const [provider, setProvider] = useState('kalshi');
  const [markets, setMarkets] = useState(null);
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState('');
  const [err, setErr] = useState(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    api.externalProviders().then((value) => setProviders(value.providers)).catch((e) => setErr(e.message));
  }, []);
  useEffect(() => {
    setSelected(null); setMarkets(null); setErr(null);
    api.externalMarkets(provider, '&limit=50').then((value) => setMarkets(value.items)).catch((e) => setErr(e.message));
  }, [provider]);

  const filtered = asArray(markets).filter((market) => !query || `${market.market_id} ${market.title}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="grid">
      <Panel title="EXTERNAL EVENT-MARKET INTELLIGENCE" sub="S3 · read-only · observed data kept separate from synthetic state">
        <div className="toolbar">
          {asArray(providers).map((item) => <ProviderRow key={item.provider_id} provider={item} selected={provider === item.provider_id} onClick={setProvider} />)}
          <input className="inp" placeholder="filter market text…" value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
        <div className="small dim section-gap">Provider metadata and market observations are labeled <span className="accent">OBSERVED</span>. Signatures are <span className="accent">INFERRED</span>; proposals are <span className="accent">HYPOTHESIS</span>; compiled experiment records are <span className="accent">MODEL_RESULT</span>. There are no trading, wallet, account, or financial-advice controls.</div>
      </Panel>
      <Detail provider={provider} market={selected} onClose={() => setSelected(null)} onDatasetRegistered={() => setRefreshToken((value) => value + 1)} />
      <Panel title={`${provider.toUpperCase()} MARKETS`} sub={markets === null ? 'loading…' : `${filtered.length} shown`} tight>
        {err && <div className="err section-gap">{err}</div>}
        <table className="t"><thead><tr><th>MARKET ID</th><th className="l">QUESTION</th><th>STATUS</th><th>YES TOKEN</th></tr></thead><tbody>
          {markets === null && <EmptyRow cols={4} text="LOADING OBSERVED MARKETS…" />}
          {markets !== null && !filtered.length && <EmptyRow cols={4} text="NO MARKETS IN PROVIDER RESPONSE" />}
          {filtered.map((market) => <MarketRow key={market.market_id} market={market} selected={selected?.market_id === market.market_id} onClick={setSelected} />)}
        </tbody></table>
      </Panel>
      <DatasetPanel provider={provider} refreshToken={refreshToken} />
    </div>
  );
}

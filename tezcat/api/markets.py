"""FastAPI routes for the External Event-Market Intelligence Layer."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from tezcat.external.datasets import DatasetStoreError
from tezcat.external.providers.base import ProviderHttpError, ProviderRateLimitError
from tezcat.external.research import (
    compare_signatures, extract_event_signature, probability_divergence,
)
from tezcat.external.schemas import (
    DataQualityError, EventSignature, ExternalDataError,
    OrderbookSnapshot, Quote, Trade, ProviderSchemaError,
)
from tezcat.external.service import ExternalMarketService


router = APIRouter(prefix="/api/markets", tags=["external-event-markets"])


class SnapshotBody(BaseModel):
    token_id: Optional[str] = None
    source_id: Optional[str] = None
    history: Optional[Dict[str, Any]] = None
    trades: Optional[Dict[str, Any]] = None
    license_terms: str = "unknown — verify provider terms before redistribution"
    permitted_use: str = "local analysis only unless provider terms state otherwise"


class SignatureBody(BaseModel):
    window: Dict[str, str] = Field(default_factory=dict)


class DivergenceBody(BaseModel):
    probability_a: float = Field(ge=0.0, le=1.0)
    probability_b: float = Field(ge=0.0, le=1.0)


class CompareBody(BaseModel):
    observed_signature: Dict[str, Any]
    synthetic_features: Dict[str, Any]


class ResearchProposalBody(BaseModel):
    dataset_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    question: str = Field(min_length=8)
    hypothesis: str = Field(min_length=8)
    mechanisms: list[str] = Field(default_factory=lambda: [
        "information_shock", "herding", "momentum", "liquidity_withdrawal",
        "mean_reversion", "market_maker_response", "agent_heterogeneity",
        "reaction_delays",
    ])
    primary_metric: str = "peak probability displacement"
    secondary_metrics: list[str] = Field(default_factory=lambda: [
        "recovery time", "spread expansion", "depth loss", "clustering", "trade activity",
    ])


class CompileResearchBody(ResearchProposalBody):
    approved: bool = False
    signature: Dict[str, Any]
    base_config: Dict[str, Any]
    experiment_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    root_seed: Optional[int] = None
    replications: int = Field(2, ge=1)


def _service(request: Request) -> ExternalMarketService:
    service = getattr(request.app.state, "external_market_service", None)
    if service is None:
        service = ExternalMarketService(request.app.state.tezcat_store)
        request.app.state.external_market_service = service
    return service


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ProviderRateLimitError):
        return HTTPException(429, str(exc))
    if isinstance(exc, ProviderHttpError):
        return HTTPException(502, str(exc))
    if isinstance(exc, (ProviderSchemaError, DataQualityError, ValueError)):
        return HTTPException(422, str(exc))
    return HTTPException(500, "external market operation failed")


@router.get("/providers")
def providers(request: Request):
    service = _service(request)
    return {
        "data_class": "OBSERVED",
        "providers": [
            {
                "provider_id": provider_id,
                "adapter_version": service.provider(provider_id).adapter_version,
                "read_only": True,
                "authentication": "public market data where documented",
                "trading_enabled": False,
            }
            for provider_id in service.provider_ids()
        ],
    }


@router.get("/events")
def list_events(request: Request, provider: str = Query("kalshi"), cursor: Optional[str] = None,
                limit: int = Query(50, ge=1, le=500), status: Optional[str] = None):
    try:
        page = _service(request).list_events(provider, cursor=cursor, limit=limit, status=status)
        adapter = _service(request).provider(provider)
        return {
            "provider": provider, "data_class": "OBSERVED", "items": [adapter.normalize_event(row).model_dump(mode="json") for row in page.items],
            "next_cursor": page.cursor, "raw_count": len(page.items),
        }
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/markets")
def list_markets(request: Request, provider: str = Query("kalshi"), cursor: Optional[str] = None,
                 limit: int = Query(50, ge=1, le=500), status: Optional[str] = None,
                 event_id: Optional[str] = None):
    try:
        service = _service(request)
        page = service.list_markets(provider, cursor=cursor, limit=limit, status=status, event_id=event_id)
        adapter = service.provider(provider)
        return {
            "provider": provider, "data_class": "OBSERVED", "items": [adapter.normalize_market(row).model_dump(mode="json") for row in page.items],
            "next_cursor": page.cursor, "raw_count": len(page.items),
        }
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/datasets")
def list_datasets(request: Request, provider: Optional[str] = None):
    return {"data_class": "OBSERVED", "items": _service(request).datasets.list(provider)}


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, request: Request):
    try:
        service = _service(request)
        return {"data_class": "OBSERVED", "manifest": service.datasets.get(dataset_id), "normalized": service.datasets.load_artifact(dataset_id, "normalized.json")}
    except DatasetStoreError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/datasets/{dataset_id}/signature")
def dataset_signature(dataset_id: str, body: SignatureBody, request: Request):
    try:
        service = _service(request)
        manifest = service.datasets.get(dataset_id)
        normalized = service.datasets.load_artifact(dataset_id, "normalized.json")
        if not isinstance(normalized, dict):
            raise ValueError("normalized dataset artifact is malformed")
        quotes = [Quote.model_validate(row) for row in normalized.get("quotes", [])]
        trades = [Trade.model_validate(row) for row in normalized.get("trades", [])]
        raw_book = normalized.get("orderbook")
        books = [OrderbookSnapshot.model_validate(raw_book)] if raw_book else []
        market_id = manifest["market_ids"][0] if manifest.get("market_ids") else "unknown"
        signature = extract_event_signature(provider=manifest["provider"], dataset_id=dataset_id, market_id=market_id, quotes=quotes, trades=trades, books=books, window=body.window)
        service.datasets.store.save_artifact(f"external/{manifest['provider']}/{manifest['source_id']}/{dataset_id}", f"signatures/{signature.signature_id}.json", signature.model_dump(mode="json"))
        return {"data_class": "INFERRED", "signature": signature.model_dump(mode="json")}
    except DatasetStoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/{market_id}")
def get_market(market_id: str, request: Request, provider: str = Query("kalshi")):
    try:
        service = _service(request)
        adapter = service.provider(provider)
        return {"data_class": "OBSERVED", "provider": provider, "market": adapter.normalize_market(service.get_market(provider, market_id)).model_dump(mode="json")}
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/{market_id}/orderbook")
def get_orderbook(market_id: str, request: Request, provider: str = Query("kalshi"), token_id: Optional[str] = None):
    try:
        service = _service(request)
        adapter = service.provider(provider)
        raw = adapter.get_orderbook(market_id, token_id=token_id) if provider == "polymarket" else adapter.get_orderbook(market_id)
        book = adapter.normalize_orderbook(raw, token_id=token_id, market_id=market_id) if provider == "polymarket" else adapter.normalize_orderbook(raw, market_id=market_id)
        return {"data_class": "OBSERVED", "provider": provider, "raw": raw, "orderbook": book.model_dump(mode="json")}
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/{market_id}/trades")
def get_trades(market_id: str, request: Request, provider: str = Query("kalshi"), limit: int = Query(100, ge=1, le=1000), cursor: Optional[str] = None):
    try:
        service = _service(request)
        page = service.provider(provider).get_trades(market_id, limit=limit, cursor=cursor)
        adapter = service.provider(provider)
        return {"data_class": "OBSERVED", "provider": provider, "items": [adapter.normalize_trade(row).model_dump(mode="json") for row in page.items], "next_cursor": page.cursor}
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/{market_id}/history")
def get_history(market_id: str, request: Request, provider: str = Query("polymarket"), token_id: Optional[str] = None,
                start_ts: Optional[int] = None, end_ts: Optional[int] = None, interval: Optional[str] = None,
                fidelity: Optional[int] = None, series_ticker: Optional[str] = None,
                period_interval: Optional[int] = None, historical: bool = False):
    try:
        service = _service(request)
        kwargs: Dict[str, Any] = {"token_id": token_id, "startTs": start_ts, "endTs": end_ts, "interval": interval, "fidelity": fidelity, "series_ticker": series_ticker, "period_interval": period_interval, "historical": historical}
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        page = service.provider(provider).get_history(market_id, **kwargs)
        adapter = service.provider(provider)
        if provider == "kalshi":
            items = [adapter.normalize_candlestick(row, market_id=market_id).model_dump(mode="json") for row in page.items]
        else:
            items = [adapter.normalize_history(row, market_id=market_id, token_id=token_id).model_dump(mode="json") for row in page.items]
        return {"data_class": "OBSERVED", "provider": provider, "items": items, "raw": page.raw}
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.post("/{market_id}/snapshot", status_code=201)
def register_snapshot(market_id: str, body: SnapshotBody, request: Request, provider: str = Query("kalshi")):
    try:
        return _service(request).snapshot(provider, market_id, **body.model_dump(exclude_none=True))
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.post("/compare")
def compare(body: CompareBody):
    try:
        observed = EventSignature.model_validate(body.observed_signature)
        return compare_signatures(observed, body.synthetic_features)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/divergence")
def divergence(body: DivergenceBody):
    return probability_divergence(body.probability_a, body.probability_b)


@router.post("/research")
def research_proposal(body: ResearchProposalBody, request: Request):
    """Return an explicit, unapproved mechanism proposal; it never runs code."""
    try:
        manifest = _service(request).datasets.get(body.dataset_id)
    except DatasetStoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "data_class": "HYPOTHESIS",
        "approved": False,
        "question": body.question,
        "hypothesis": body.hypothesis,
        "dataset_id": body.dataset_id,
        "market_id": body.market_id,
        "mechanisms": body.mechanisms,
        "design": {"vary": ["mechanism", "intensity", "liquidity"], "primary_metric": body.primary_metric, "secondary_metrics": body.secondary_metrics},
        "source": {"provider": manifest["provider"], "dataset_hash": manifest["normalized_checksum"]},
        "language": ["candidate mechanism", "synthetic analogue", "observational association", "not a causal claim"],
        "next_step": "A researcher must approve and compile this proposal into an ordinary ExperimentVersion.",
    }


@router.post("/research/compile", status_code=201)
def compile_research(body: CompileResearchBody, request: Request):
    if not body.approved:
        raise HTTPException(409, "research proposal requires explicit approval before compilation")
    try:
        from tezcat.core.config import ExperimentConfig
        from tezcat.external.bridge import compile_event_experiment
        signature = EventSignature.model_validate(body.signature)
        try:
            dataset_manifest = _service(request).datasets.get(body.dataset_id)
        except DatasetStoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        if dataset_manifest.get("provider") != signature.provider:
            raise ValueError("signature provider does not match dataset provider")
        if signature.dataset_id != body.dataset_id:
            raise ValueError("signature dataset_id does not match proposal dataset_id")
        config = ExperimentConfig.model_validate(body.base_config)
        result = compile_event_experiment(
            base_config=config, signature=signature,
            experiment_id=body.experiment_id, name=body.name,
            question=body.question, hypothesis=body.hypothesis,
            root_seed=body.root_seed, replications=body.replications,
            primary_metric=body.primary_metric,
            dataset_manifest=dataset_manifest,
        )
        version = result["experiment"]
        version_id = request.app.state.research_registry.register(version)
        persisted_manifest = request.app.state.research_registry.save_external_manifest(
            version_id, result["manifest"]
        )
        return {
            "data_class": "MODEL_RESULT",
            "version_id": version_id,
            "experiment": version.to_dict(),
            "external_research_manifest": persisted_manifest,
            "warning": "The external signature is research context; it did not mutate the synthetic kernel.",
        }
    except Exception as exc:
        raise _provider_error(exc) from exc

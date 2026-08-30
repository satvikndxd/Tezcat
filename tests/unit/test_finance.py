"""Finance domain (S6): golden values, invariants, loud failures."""

import pytest

from tezcat.finance.comps import CompsSelection, PeerCompany, run_comps
from tezcat.finance.dcf import DCFAssumptions, run_dcf
from tezcat.finance.forecast import ForecastAssumptions, build_forecast
from tezcat.finance.proforma import EarningsGrowthAssumptions, build_pro_forma
from tezcat.finance.statements import (
    CompanyFinancials, FinanceError, FiscalPeriod, Provenance,
)
from tezcat.finance.transaction import (
    SynergyAssumptions, TransactionAssumptions, structure_transaction,
)

PROV = Provenance(source="synthetic fixture")


def period(label, rev, **kw):
    base = dict(label=label, revenue=rev, cogs=rev * 0.5, opex=rev * 0.2,
                depreciation_amortization=rev * 0.05,
                interest_expense=10.0, taxes=20.0, capex=rev * 0.1,
                net_working_capital=rev * 0.10, cash=50.0, debt=150.0,
                diluted_shares=100.0, provenance=PROV)
    base.update(kw)
    return FiscalPeriod(**base)


def company(name="TestCo", price=20.0, **latest_kw):
    return CompanyFinancials(
        name=name, share_price=price,
        periods=[period("FY1", 900.0), period("FY2", 1000.0, **latest_kw)])


@pytest.fixture()
def assumptions():
    return ForecastAssumptions(
        revenue_growth=[0.10, 0.10], ebitda_margin=[0.30, 0.30],
        da_pct_revenue=[0.05, 0.05], capex_pct_revenue=[0.10, 0.10],
        nwc_pct_revenue=[0.10, 0.10], tax_rate=0.25)


class TestStatements:
    def test_derived_income_statement(self):
        p = period("FY", 1000.0)
        assert p.gross_profit == 500.0
        assert p.ebitda == 300.0
        assert p.ebit == 250.0
        assert p.net_income == 250.0 - 10.0 - 20.0
        assert p.net_debt == 100.0

    def test_cogs_above_revenue_fails(self):
        with pytest.raises(FinanceError, match="COGS"):
            period("FY", 1000.0, cogs=1100.0)

    def test_fcf_requires_prior_period(self):
        c = company()
        with pytest.raises(FinanceError, match="prior period"):
            c.unlevered_fcf(0, 0.25)
        # FY2: EBIT 250 * .75 + D&A 50 - capex 100 - ΔNWC (100-90) = 127.5
        assert c.unlevered_fcf(1, 0.25) == pytest.approx(127.5)

    def test_duplicate_periods_fail(self):
        with pytest.raises(FinanceError, match="duplicate"):
            CompanyFinancials(name="X", periods=[period("FY1", 900.0),
                                                 period("FY1", 950.0)])

    def test_market_derived(self):
        c = company()
        assert c.market_cap() == 2000.0
        assert c.enterprise_value() == 2100.0
        assert c.content_hash() == company().content_hash()


class TestForecast:
    def test_golden_projection(self, assumptions):
        proj = build_forecast(company(), assumptions)
        y1, y2 = proj
        assert y1.revenue == pytest.approx(1100.0)
        assert y1.ebitda == pytest.approx(330.0)
        assert y1.ebit == pytest.approx(275.0)
        assert y1.nopat == pytest.approx(206.25)
        assert y1.change_in_nwc == pytest.approx(10.0)  # 110 - anchor 100
        assert y1.unlevered_fcf == pytest.approx(141.25)
        assert y2.unlevered_fcf == pytest.approx(155.375)

    def test_vector_length_mismatch_fails(self):
        with pytest.raises(FinanceError, match="horizon"):
            ForecastAssumptions(revenue_growth=[0.1, 0.1],
                                ebitda_margin=[0.3],
                                da_pct_revenue=[0.05, 0.05],
                                capex_pct_revenue=[0.1, 0.1],
                                nwc_pct_revenue=[0.1, 0.1], tax_rate=0.25)


class TestDCF:
    def test_golden_gordon_growth(self, assumptions):
        c = company()
        proj = build_forecast(c, assumptions)
        result = run_dcf(c, proj, DCFAssumptions(
            wacc=0.10, terminal_method="gordon_growth",
            terminal_growth=0.02))
        # hand-computed: PV1 = 141.25/1.1, PV2 = 155.375/1.21
        assert result["pv_forecast_fcf"] == pytest.approx(256.818182, abs=1e-4)
        # TV = 155.375*1.02/0.08 = 1981.03125 → PV = /1.21
        assert result["terminal_value_undiscounted"] == pytest.approx(
            1981.03125, abs=1e-4)
        assert result["pv_terminal_value"] == pytest.approx(1637.215909,
                                                            abs=1e-4)
        assert result["enterprise_value"] == pytest.approx(1894.034091,
                                                           abs=1e-4)
        # invariant: EV = PV(FCF) + PV(TV)
        assert result["enterprise_value"] == pytest.approx(
            result["pv_forecast_fcf"] + result["pv_terminal_value"], abs=1e-6)
        # equity bridge: EV − net debt (100) → per share (/100)
        assert result["equity_value"] == pytest.approx(1794.034091, abs=1e-4)
        assert result["implied_value_per_share"] == pytest.approx(
            17.940341, abs=1e-5)

    def test_golden_exit_multiple(self, assumptions):
        c = company()
        proj = build_forecast(c, assumptions)
        result = run_dcf(c, proj, DCFAssumptions(
            wacc=0.10, terminal_method="exit_multiple", exit_multiple=8.0))
        assert result["terminal_value_undiscounted"] == pytest.approx(
            363.0 * 8)
        assert result["enterprise_value"] == pytest.approx(
            256.818182 + 2904.0 / 1.21, abs=1e-4)

    def test_terminal_growth_must_be_below_wacc(self):
        with pytest.raises(FinanceError, match="strictly below WACC"):
            DCFAssumptions(wacc=0.06, terminal_method="gordon_growth",
                           terminal_growth=0.06)

    def test_negative_terminal_fcf_refused(self, assumptions):
        broken = assumptions.model_copy(
            update={"capex_pct_revenue": [0.10, 0.60]})
        c = company()
        with pytest.raises(FinanceError, match="Gordon"):
            run_dcf(c, build_forecast(c, broken), DCFAssumptions(
                wacc=0.10, terminal_method="gordon_growth",
                terminal_growth=0.02))

    def test_terminal_share_disclosed(self, assumptions):
        c = company()
        result = run_dcf(c, build_forecast(c, assumptions), DCFAssumptions(
            wacc=0.10, terminal_method="gordon_growth", terminal_growth=0.02))
        assert result["terminal_value_pct_of_ev"] == pytest.approx(
            result["pv_terminal_value"] / result["enterprise_value"])


class TestComps:
    def peers(self):
        def peer(name, ebitda, price):
            return PeerCompany(name=name, revenue=1000.0, ebitda=ebitda,
                               ebit=ebitda * 0.8, net_income=ebitda * 0.5,
                               cash=100.0, debt=300.0, diluted_shares=100.0,
                               share_price=price, provenance=PROV)
        # EVs: mktcap + 200 → 2200, 3200, 4200; EV/EBITDA: 11, 8, 7
        return [peer("A", 200.0, 20.0), peer("B", 400.0, 30.0),
                peer("C", 600.0, 40.0)]

    def test_golden_median_ev_ebitda(self):
        result = run_comps(company(), self.peers(),
                           CompsSelection(multiple="ev_ebitda",
                                          statistic="median"))
        assert result["statistics"]["ev_ebitda"]["median"] == pytest.approx(8.0)
        # target EBITDA 300 → EV 2400 → equity 2300 → 23.0/share
        assert result["implied_enterprise_value"] == pytest.approx(2400.0)
        assert result["implied_value_per_share"] == pytest.approx(23.0)

    def test_negative_denominator_peer_excluded_not_clamped(self):
        peers = self.peers()
        peers.append(PeerCompany(name="LossCo", revenue=500.0, ebitda=-50.0,
                                 ebit=-80.0, net_income=-60.0, cash=50.0,
                                 debt=10.0, diluted_shares=50.0,
                                 share_price=5.0, provenance=PROV))
        result = run_comps(company(), peers, CompsSelection())
        assert result["statistics"]["ev_ebitda"]["excluded"] == 1

    def test_too_few_peers_fails(self):
        with pytest.raises(FinanceError, match=">= 3 peers"):
            run_comps(company(), self.peers()[:2], CompsSelection())


class TestTransactionGolden:
    def acquirer(self):
        return CompanyFinancials(
            name="Acq", share_price=50.0,
            periods=[period("FY1", 4500.0, cash=550.0, debt=500.0,
                            diluted_shares=100.0),
                     period("FY2", 5000.0, cash=600.0, debt=500.0,
                            diluted_shares=100.0, taxes=90.0,
                            interest_expense=25.0,
                            depreciation_amortization=135.0)])

    def target(self):
        return company(price=20.0, diluted_shares=50.0)

    def assumptions(self, **kw):
        base = dict(premium_pct=0.25, pct_stock=0.40, new_debt=400.0,
                    advisory_fees=20.0, financing_fees=10.0,
                    financing_fee_amortization_years=5,
                    cost_of_new_debt=0.05, cash_yield=0.03,
                    minimum_cash=100.0, tax_rate=0.25,
                    synergies=SynergyAssumptions(run_rate_pretax=30.0,
                                                 phase_in=[1.0]))
        base.update(kw)
        return TransactionAssumptions(**base)

    def test_golden_structure(self):
        txn = structure_transaction(self.acquirer(), self.target(),
                                    self.assumptions())
        assert txn["offer_price_per_share"] == pytest.approx(25.0)
        assert txn["equity_purchase_price"] == pytest.approx(1250.0)
        # purchase EV = 1250 + target net debt 100
        assert txn["purchase_enterprise_value"] == pytest.approx(1350.0)
        assert txn["sources"]["stock_consideration"] == pytest.approx(500.0)
        # bs cash = uses(1280) − debt(400) − stock(500) = 380
        assert txn["sources"]["acquirer_balance_sheet_cash"] == \
            pytest.approx(380.0)
        assert txn["total_sources"] == pytest.approx(txn["total_uses"])
        assert txn["new_shares_issued"] == pytest.approx(10.0)  # 500 / 50
        own = txn["ownership"]
        assert own["acquirer_shareholders"] + own["target_shareholders"] == \
            pytest.approx(1.0)
        assert own["target_shareholders"] == pytest.approx(10.0 / 110.0)

    def test_unfundable_structure_fails(self):
        with pytest.raises(FinanceError, match="not fundable"):
            structure_transaction(self.acquirer(), self.target(),
                                  self.assumptions(minimum_cash=500.0))

    def test_overfunded_structure_fails(self):
        with pytest.raises(FinanceError, match="over-funded"):
            structure_transaction(self.acquirer(), self.target(),
                                  self.assumptions(new_debt=2000.0))

    def test_exactly_one_price_spec(self):
        with pytest.raises(FinanceError, match="exactly one"):
            self.assumptions(offer_price_per_share=25.0)

    def test_golden_accretion(self):
        acq, tgt = self.acquirer(), self.target()
        txn = structure_transaction(acq, tgt, self.assumptions())
        pf = build_pro_forma(acq, tgt, txn, EarningsGrowthAssumptions(
            acquirer_growth=[0.0], target_growth=[0.0]))
        y1 = pf["years"][0]
        # acquirer FY2 NI: EBITDA 1500 − D&A 135 − int 25 − tax 90 = 1250
        assert y1["bridge"]["acquirer_standalone_ni"] == pytest.approx(1250.0)
        # target FY2 NI: 300 − 50 − 10 − 20 = 220
        assert y1["bridge"]["target_standalone_ni"] == pytest.approx(220.0)
        assert y1["bridge"]["after_tax_synergies"] == pytest.approx(22.5)
        assert y1["bridge"]["after_tax_incremental_interest"] == \
            pytest.approx(-15.0)
        assert y1["bridge"]["after_tax_foregone_cash_interest"] == \
            pytest.approx(-380 * 0.03 * 0.75)
        assert y1["bridge"]["after_tax_financing_fee_amortization"] == \
            pytest.approx(-1.5)
        # invariant: bridge sums exactly to pro forma NI
        assert y1["pro_forma_net_income"] == pytest.approx(
            sum(y1["bridge"].values()))
        # accretion derived from EPS exactly
        assert y1["accretion_dilution_pct"] == pytest.approx(
            y1["pro_forma_eps"] / y1["standalone_eps"] - 1.0, abs=1e-5)
        assert y1["verdict"] == "accretive"

    def test_negative_acquirer_earnings_refused(self):
        acq = self.acquirer()
        broken = acq.model_copy(update={"periods": [
            acq.periods[0], acq.periods[1].model_copy(
                update={"taxes": 2000.0, "cogs": 4000.0})]})
        # cogs 4000 < revenue 5000 passes statement check; NI is negative
        txn = structure_transaction(acq, self.target(), self.assumptions())
        with pytest.raises(FinanceError, match="not meaningful"):
            build_pro_forma(broken, self.target(), txn,
                            EarningsGrowthAssumptions(acquirer_growth=[0.0],
                                                      target_growth=[0.0]))

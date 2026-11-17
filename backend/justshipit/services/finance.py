"""Deterministic finance engine. The CFO agent supplies assumptions; this code does the math."""
from __future__ import annotations

from dataclasses import dataclass

from justshipit.contracts.work import FinancialModelOut, FinancialScenarioOut
from justshipit.i18n import Lang, msg


@dataclass
class ScenarioResult:
    name: str
    months: int
    end_customers: float
    total_revenue: float
    total_cost: float
    cumulative_cash_min: float
    cumulative_cash_end: float
    breakeven_month: int | None
    ltv: float | None
    cac: float
    ltv_to_cac: float | None
    payback_months: float | None
    monthly: list[dict]


def run_scenario(sc: FinancialScenarioOut) -> ScenarioResult:
    months = max(1, min(int(sc.months), 120))
    customers = max(0.0, sc.customers_month_1)
    cash = 0.0
    cash_min = 0.0
    breakeven = None
    rows = []
    total_rev = total_cost = 0.0
    for m in range(1, months + 1):
        if m > 1:
            new = customers * sc.monthly_customer_growth_rate
            churned = customers * sc.monthly_churn_rate
            customers = max(0.0, customers + new - churned)
        else:
            new = customers
        revenue = customers * sc.price_per_customer_month
        variable = customers * sc.variable_cost_per_customer_month
        acquisition = max(0.0, new) * sc.acquisition_cost_per_customer
        cost = variable + sc.fixed_cost_month + acquisition
        net = revenue - cost
        cash += net
        cash_min = min(cash_min, cash)
        total_rev += revenue
        total_cost += cost
        if breakeven is None and net >= 0 and m > 1:
            breakeven = m
        rows.append({"month": m, "customers": round(customers, 1), "revenue": round(revenue),
                     "cost": round(cost), "net": round(net), "cumulative_cash": round(cash)})
    margin = sc.price_per_customer_month - sc.variable_cost_per_customer_month
    ltv = margin / sc.monthly_churn_rate if sc.monthly_churn_rate > 0 else None
    payback = sc.acquisition_cost_per_customer / margin if margin > 0 else None
    return ScenarioResult(
        name=sc.name, months=months, end_customers=round(customers, 1), total_revenue=round(total_rev),
        total_cost=round(total_cost), cumulative_cash_min=round(cash_min), cumulative_cash_end=round(cash),
        breakeven_month=breakeven, ltv=round(ltv) if ltv is not None else None,
        cac=sc.acquisition_cost_per_customer,
        ltv_to_cac=round(ltv / sc.acquisition_cost_per_customer, 2) if ltv and sc.acquisition_cost_per_customer else None,
        payback_months=round(payback, 1) if payback is not None else None, monthly=rows,
    )


def run_model(model: FinancialModelOut, lang: Lang | None = None) -> tuple[list[ScenarioResult], str, list[str]]:
    """The scenarios computed, with their table, one claim per scenario and the assumptions, in `lang` (by default
    the language the owner reads now)."""
    if lang is None:
        from justshipit.services import app_settings
        lang = app_settings.language()
    results = [run_scenario(sc) for sc in model.scenarios]
    cur = model.currency
    never = msg("finance.not_reached", lang)
    md = [msg("finance.header", lang), "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    claims = []
    for r in results:
        md.append(f"| {r.name} | {r.months} | {r.end_customers:,} | {r.total_revenue:,} | {r.total_cost:,} | "
                  f"{r.cumulative_cash_min:,} | {r.cumulative_cash_end:,} | {r.breakeven_month or never} | "
                  f"{r.ltv if r.ltv is not None else '-'} | {r.cac:,} | {r.ltv_to_cac or '-'} | "
                  f"{r.payback_months or '-'} |")
        claims.append(msg("finance.claim", lang, name=r.name, months=r.months, revenue=f"{r.total_revenue:,}", cur=cur,
                          cash_min=f"{r.cumulative_cash_min:,}", breakeven=r.breakeven_month or never,
                          ratio=r.ltv_to_cac or "-"))
    assumptions = "\n".join(
        msg("finance.assumption", lang, name=sc.name, price=f"{sc.price_per_customer_month:,}",
            first=f"{sc.customers_month_1:g}", growth=f"{sc.monthly_customer_growth_rate:.1%}",
            churn=f"{sc.monthly_churn_rate:.1%}", variable=f"{sc.variable_cost_per_customer_month:,}",
            fixed=f"{sc.fixed_cost_month:,}", cac=f"{sc.acquisition_cost_per_customer:,}",
            notes="; ".join(sc.assumption_notes))
        for sc in model.scenarios)
    table = "\n".join(md) + "\n\n" + msg("finance.unit", lang, cur=cur) + "\n\n" + assumptions
    return results, table, claims

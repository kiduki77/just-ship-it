"""The financial model (`services/finance.py`): the scenario table, its one-line claims and the assumptions under it."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "finance.header": {
        "en": "| Scenario | Months | Customers at end | Total revenue | Total cost | Lowest cumulative cash | Cumulative cash at end | Month it turns profitable | LTV | CAC | LTV/CAC | Payback (months) |",
        "ko": "| 시나리오 | 기간(월) | 마지막 달 고객 수 | 누적 매출 | 누적 비용 | 최저 누적 현금 | 마지막 누적 현금 | 월 흑자 전환 | LTV | CAC | LTV/CAC | 회수 기간(월) |"},
    "finance.not_reached": {"en": "not reached", "ko": "도달 못 함"},
    "finance.claim": {
        "en": "[{name}] Over {months} months: revenue {revenue} {cur}, lowest cumulative cash {cash_min} {cur}, month it turns profitable: {breakeven}, LTV/CAC {ratio} (computed from the assumptions given)",
        "ko": "[{name}] {months}개월 누적 매출 {revenue} {cur}, 최저 누적 현금 {cash_min} {cur}, 월 흑자 전환 {breakeven}, LTV/CAC {ratio} (입력한 가정으로 계산)"},
    "finance.assumption": {
        "en": "- **{name}**: price {price}/month, first-month customers {first}, monthly growth {growth}, monthly churn {churn}, variable cost {variable}/customer, fixed cost {fixed}/month, CAC {cac}: {notes}",
        "ko": "- **{name}**: 가격 {price}/월, 첫 달 고객 {first}, 월 성장 {growth}, 월 이탈 {churn}, 변동비 {variable}/고객, 고정비 {fixed}/월, CAC {cac}: {notes}"},
    "finance.unit": {"en": "Unit: {cur}. Code computed this from the assumptions below.",
                     "ko": "단위: {cur}. 아래 가정으로 코드가 계산했어요."},
}

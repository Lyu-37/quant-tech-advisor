"""Buy-suppression policy: fail-closed on bad data + account circuit breaker.

A professional system CLOSES on bad inputs instead of warning and carrying
on; and it stops adding risk when the account itself says stop. Both
conditions funnel into one `buy_suppression` string consumed by the
renderers — when set, every dollar-carrying buy field is replaced with the
reason. Market-state description is unaffected (information still flows;
only the trading instructions stop).
"""
from . import config
from .ledger import SleeveStatus


def data_quality_suppression(fresh_warn: str | None,
                             n_failed: int, n_total: int) -> str | None:
    """Fail-closed: data not current, or too many fetch failures."""
    if not config.get("data_quality.fail_closed", True):
        return None
    reasons = []
    if fresh_warn:
        reasons.append("数据未达最新交易日")
    max_fail = float(config.get("data_quality.max_failure_rate", 0.05))
    if n_total > 0 and (n_failed / n_total) > max_fail:
        reasons.append(f"取数失败 {n_failed}/{n_total} 超过阈值")
    return " + ".join(reasons) if reasons else None


def breaker_suppression(sleeve: SleeveStatus | None) -> str | None:
    if sleeve is not None and sleeve.breaker_active:
        return (f"投机桶熔断中 (距高水位 {sleeve.drawdown_pct * 100:.0f}%), "
                f"暂停新买入至 {sleeve.breaker_until}")
    return None


def combine(*parts: str | None) -> str | None:
    reasons = [p for p in parts if p]
    return "; ".join(reasons) if reasons else None

"""Aggregate scoring across the 5 analytical layers."""
from dataclasses import dataclass, field
import pandas as pd

from .indicators import (
    trend_alignment,
    momentum_score,
    relative_strength,
    vol_regime_score,
    macro_pressure_score,
)


# Layer weights (must sum to 1.0)
LAYER_WEIGHTS = {
    "trend":      0.25,
    "momentum":   0.20,
    "rel_strength": 0.15,
    "vol_regime": 0.15,
    "macro":      0.25,
}


@dataclass
class SectorScore:
    trend: dict
    momentum: dict
    rel_strength: dict
    vol_regime: dict
    macro: dict
    composite_0_100: float = 0.0
    label: str = ""
    sub_scores: dict = field(default_factory=dict)


def composite_sector_score(
    sector_close: pd.Series,         # SMH or SOXX close series
    benchmark_close: pd.Series,      # SPY close
    vix_close: pd.Series | None,
    tnx_df: pd.DataFrame | None,
    dxy_df: pd.DataFrame | None,
) -> SectorScore:
    """Aggregate the 5 layers into a 0-100 composite score for the semi sector."""
    trend = trend_alignment(sector_close)
    mom = momentum_score(sector_close)
    rs = relative_strength(sector_close, benchmark_close)
    vol = vol_regime_score(
        sector_close,
        vix=float(vix_close.iloc[-1]) if vix_close is not None and not vix_close.empty else None,
    )
    macro = macro_pressure_score(tnx_df, dxy_df)

    sub = {
        "trend":        trend["score"],
        "momentum":     mom["score"],
        "rel_strength": rs["score"],
        "vol_regime":   vol["score"],
        "macro":        macro["score"],
    }
    # Weighted average -> 0-10 -> 0-100
    weighted = sum(sub[k] * LAYER_WEIGHTS[k] for k in LAYER_WEIGHTS)
    composite = float(weighted * 10)

    if composite >= 75:
        label = "强势 (strong tailwind)"
    elif composite >= 60:
        label = "偏多 (constructive)"
    elif composite >= 40:
        label = "中性 (neutral)"
    elif composite >= 25:
        label = "偏空 (caution)"
    else:
        label = "弱势 (strong headwind)"

    return SectorScore(
        trend=trend,
        momentum=mom,
        rel_strength=rs,
        vol_regime=vol,
        macro=macro,
        composite_0_100=composite,
        label=label,
        sub_scores=sub,
    )

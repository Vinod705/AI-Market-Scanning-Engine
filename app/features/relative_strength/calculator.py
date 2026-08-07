"""Relative strength vs a benchmark index.

Unlike every other category, RS needs a second price series (the
benchmark), so `calculate` takes an extra `benchmark_df` argument instead of
following the single-`df` contract the other calculators share.

`rs_vs_sector` / `sector_rank` are NOT computed here: ranking a symbol
against its sector peers is inherently cross-sectional (it needs every
symbol's data at once, not just this one), so that lives in
`app.features.engine.FeatureEngine` after all symbols' daily features are
computed. They also require `Symbol.sector` to be populated, which the
5paisa scrip master doesn't currently provide — both columns stay `None`
until a data source for sector classification exists.
"""

import pandas as pd

_LOOKBACK_DAYS = 20


class RelativeStrengthFeatureCalculator:
    @staticmethod
    def calculate(df: pd.DataFrame, benchmark_df: pd.DataFrame | None) -> pd.DataFrame:
        if benchmark_df is None or benchmark_df.empty:
            return pd.DataFrame({"rs_vs_nifty": pd.Series(index=df.index, dtype="float64")})

        symbol_return = df["close"].pct_change(periods=_LOOKBACK_DAYS) * 100
        benchmark_return = (benchmark_df["close"].pct_change(periods=_LOOKBACK_DAYS) * 100).reindex(
            df.index
        )

        rs_vs_nifty = symbol_return - benchmark_return

        return pd.DataFrame({"rs_vs_nifty": rs_vs_nifty}, index=df.index)

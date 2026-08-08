"""The only `FundamentalDataProvider` currently wired into this project.

py5paisa (the only market-data SDK integrated so far) exposes zero
fundamentals/company-info endpoints — verified by live introspection of
the installed client (only trading/order/quote/historical-candle methods
exist). Screener.in was evaluated as a possible source and rejected: it
has no authorized API or developer program for third-party programmatic
access, and scraping it is out of scope (see the module docstring on
`app.fundamentals.models` for the full evaluation).

So, honestly: there is currently no fundamental-data source integrated.
This provider says exactly that — it always returns `None` — instead of
inventing plausible-looking numbers. `app.fundamentals.scorer` reads
`None` and reports the symbol's Fundamental Score as UNKNOWN, not as a
low score, which is an important distinction (no data is not the same as
bad data).

Swap in a real provider later by implementing `FundamentalDataProvider`
in a new module and constructing it instead of this class at startup in
`app/main.py` — no other file needs to change.
"""

from app.fundamentals.models import FundamentalData
from app.fundamentals.provider import FundamentalDataProvider


class UnavailableFundamentalDataProvider(FundamentalDataProvider):
    name = "unavailable"

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        return None

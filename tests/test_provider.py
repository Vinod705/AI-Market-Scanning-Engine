"""Tests for the provider abstraction and the 5paisa implementation's plumbing."""

import time

import pytest

from app.config.settings import Settings
from app.providers.base_provider import (
    Candle,
    MarketDataProvider,
    ProviderError,
    ProviderSymbol,
    Quote,
)
from app.providers.fivepaisa_provider import FivePaisaProvider


def test_market_data_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        MarketDataProvider()  # type: ignore[abstract]


class DummyProvider(MarketDataProvider):
    """A minimal conforming implementation, used to prove the ABC contract is usable."""

    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def get_symbols(self) -> list[ProviderSymbol]:
        return [ProviderSymbol(symbol="TCS", exchange="N", instrument_token="11536")]

    async def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    async def get_intraday(self, symbol: str) -> list[Candle]:
        return []

    async def get_daily(self, symbol: str) -> list[Candle]:
        return []


async def test_dummy_provider_conforms_to_contract() -> None:
    provider = DummyProvider()
    assert provider.is_connected() is False

    await provider.connect()
    assert provider.is_connected() is True

    symbols = await provider.get_symbols()
    assert symbols[0].symbol == "TCS"

    await provider.disconnect()
    assert provider.is_connected() is False


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "fivepaisa_app_name": "app",
        "fivepaisa_app_source": "src",
        "fivepaisa_user_id": "uid",
        "fivepaisa_password": "pwd",
        "fivepaisa_user_key": "key",
        "fivepaisa_encryption_key": "enc",
        "fivepaisa_client_code": "code",
        "fivepaisa_pin": "1234",
        "fivepaisa_totp_secret": "JBSWY3DPEHPK3PXP",
        "fivepaisa_rate_limit_per_sec": 5.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_fivepaisa_provider_starts_disconnected() -> None:
    provider = FivePaisaProvider(_settings())
    assert provider.is_connected() is False


async def test_fivepaisa_provider_rejects_connect_without_credentials() -> None:
    provider = FivePaisaProvider(_settings(fivepaisa_client_code=""))
    with pytest.raises(ProviderError):
        await provider.connect()


async def test_fivepaisa_provider_rate_limiter_spaces_out_calls() -> None:
    provider = FivePaisaProvider(_settings(fivepaisa_rate_limit_per_sec=10.0))  # 100ms min interval

    start = time.monotonic()
    await provider._respect_rate_limit()
    await provider._respect_rate_limit()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.09  # allow small scheduling jitter below the 100ms floor


async def test_resolve_symbol_raises_for_unknown_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FivePaisaProvider(_settings())

    async def _empty_get_symbols() -> list[ProviderSymbol]:
        return []

    monkeypatch.setattr(provider, "get_symbols", _empty_get_symbols)

    with pytest.raises(ProviderError, match="Unknown symbol"):
        await provider._resolve_symbol("NOPE")

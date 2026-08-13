"""Generated Protobuf bindings for Upstox's Market Data Feed V3.

`MarketDataFeed.proto` is the real, unmodified schema fetched from
`https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto`.
`MarketDataFeed_pb2.py` is generated from it via:

    python -m grpc_tools.protoc -I app/providers/upstox_proto \
        --python_out=app/providers/upstox_proto \
        app/providers/upstox_proto/MarketDataFeed.proto

Never hand-edit `MarketDataFeed_pb2.py` — regenerate it if the upstream
schema changes. Excluded from ruff/mypy (see pyproject.toml) since it's
generated code, not authored here.
"""

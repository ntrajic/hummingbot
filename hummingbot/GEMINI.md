# Hummingbot Core Instructions

This directory contains the core logic for Hummingbot, including connectors, strategies, and core utilities.

## Directory Structure
- `connector/`: Exchange and derivative connectors.
- `strategy/`: V1 strategy implementations.
- `strategy_v2/`: V2 strategy implementations using controllers.
- `core/`: Base classes and low-level utilities.

## Connector Development
- Inherit from `ExchangePyBase` or `PerpetualDerivativePyBase` for new connectors.
- Use `InFlightOrderBase` for order tracking.
- Follow the patterns in `hummingbot/connector/exchange/binance`.

## Architecture Notes
- Hummingbot uses an asynchronous, event-driven architecture.
- Many performance-critical components are written in Cython (`.pyx` files).

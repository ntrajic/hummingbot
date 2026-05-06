# Hummingbot Test Instructions

This directory contains the test suite for Hummingbot.

## Structure
The directory structure mirrors the `hummingbot/` directory. For every file in `hummingbot/`, there should be a corresponding test file in `test/`.

## Test Types
- **Unit Tests**: Test individual components in isolation.
- **Integration Tests**: Test the interaction between multiple components (e.g., `AbstractExchangeConnectorTests`).

## Conventions
- Use `IsolatedAsyncioWrapperTestCase` for asynchronous tests.
- Use `aioresponses` to mock HTTP requests.
- Use `unittest.mock` for mocking other dependencies.

## Running Tests
- Run all tests: `pytest test`
- Run a specific file: `pytest test/hummingbot/connector/exchange/test_binance_exchange.py`
- Run a specific test class: `pytest test/hummingbot/connector/exchange/test_binance_exchange.py::BinanceExchangeTests`

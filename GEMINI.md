# Hummingbot Project Instructions

This file contains instructions and conventions for working on the Hummingbot codebase.

## Project Overview
Hummingbot is an open-source framework for building and running crypto trading bots. It supports various exchanges (connectors) and strategies.

## Subdirectory Instructions
- [Hummingbot Core](./hummingbot/GEMINI.md): Instructions for the core codebase.
- [Testing Guide](./test/GEMINI.md): Instructions for writing and running tests.

## Testing Conventions
- The project uses `unittest` and `pytest`.
- Many exchange connector tests inherit from `AbstractExchangeConnectorTests` in `hummingbot/connector/test_support/exchange_connector_test.py`.
- Tests are located in the `test/` directory.
- Use `pytest` to run tests: `pytest test/path/to/test_file.py`

## Development Workflow
- Follow the Research -> Strategy -> Execution lifecycle.
- Ensure all changes are verified with tests.
- Adhere to the existing coding style and patterns found in the codebase.

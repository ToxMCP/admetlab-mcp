# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-07-21

### Added

- Open-source governance and release docs (`SECURITY.md`, `CODE_OF_CONDUCT.md`, issue/PR templates, release checklist).
- MCP transport regression coverage for initialize capability negotiation, initialized notifications, and successful tool-call responses.

### Changed

- Improved JSON-RPC validation/error handling and settings parsing.
- Cleaned package metadata and project environment template.

### Fixed

- Advertised the MCP `tools` capability as an object instead of a boolean so strict clients such as Claude Desktop through `mcp-remote` can complete initialization.
- Accepted the standard `notifications/initialized` method and acknowledged HTTP notifications with `202 Accepted` and no response body.
- Returned successful tool calls as MCP content blocks with matching `structuredContent` instead of placing a raw object in `content`.

## [0.1.0] - 2026-02-06

### Added

- Initial ADMETlab MCP HTTP server implementation.
- Tools: `wash_molecule`, `render_molecule_svg`, `predict_admet`, `fetch_admet_csv`.

# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

### Added

- Go module with the `taskwire` binary: `version` works; `migrate`, `check`
  and `controller` are reserved and exit non-zero.
- Controller configuration from environment variables (`TASKWIRE_BACKEND_URL`,
  `TASKWIRE_STARTUP_CHECK`, `TASKWIRE_ALLOW_EPHEMERAL`) with errors that name
  the variable and never echo its value.
- PostgreSQL integration-test fixture for Go, `make db-up` / `make db-down`,
  and CI jobs on PostgreSQL 14 and 18.
- `clients/` for per-language libraries. The Python library is a skeleton
  (`python -m taskwire version`, `TASKWIRE_CONTROLLER_URL` and
  `TASKWIRE_API_TOKEN` settings); Go, Rust and Java are placeholders.

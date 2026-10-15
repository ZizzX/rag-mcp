# Onboarding guide

Welcome to the team. This document explains the first week.

## Accounts

On day one you receive a corporate email and a VPN certificate. The VPN
certificate expires after 90 days and must be renewed through the IT portal.

## Development environment

We use `uv` for Python projects and `pnpm` for JavaScript. Docker Desktop is
required for running the local database. Ask in #dev-help if the setup script
fails.

### Secrets

Never commit secrets. Local secrets live in `.env` files which are ignored by
git. Shared secrets are stored in the password manager under the "Engineering"
vault.

## Holidays

Vacation requests are submitted in the HR system at least two weeks in advance.
The yearly allowance is 28 days.

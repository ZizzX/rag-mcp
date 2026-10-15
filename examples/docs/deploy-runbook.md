# Deploy runbook

## Preconditions

CI must be green on the release commit. Database migrations are reviewed by a
second engineer.

## Rollback

To roll back, redeploy the previous image tag with `deploy --tag <previous>`.
Rollback must happen within 15 minutes of detecting a regression. Never roll
back a migration that dropped a column; restore from the nightly backup instead.

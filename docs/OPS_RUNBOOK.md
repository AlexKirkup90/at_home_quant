# Ops Runbook

## Environment promotion flow

1. Run release gates:
   - `python -m at_home_quant.scripts.run_release_gates`
2. Propose release from succeeded experiment:
   - `python -m at_home_quant.scripts.manage_model_release propose --model <model> --env stage --experiment-id <id>`
3. Approve release:
   - `python -m at_home_quant.scripts.manage_model_release approve --release-id <id>`
4. Activate release:
   - `python -m at_home_quant.scripts.manage_model_release activate --release-id <id>`
5. Verify active release:
   - `python -m at_home_quant.scripts.manage_model_release active --model <model> --env stage`

Repeat for `prod` once stage validation is complete.

## Incident handling

1. Identify active release:
   - `python -m at_home_quant.scripts.manage_model_release active --model <model> --env prod`
2. Roll back to prior approved release:
   - `python -m at_home_quant.scripts.manage_model_release rollback --model <model> --env prod --target-release-id <previous_id>`
3. Re-run release gates and verify backend run status.
4. Capture incident context in release notes and audit log events.

## Audit verification

Audit entries are stored in `audit_events` and hash-chained (`prev_hash` => `event_hash`).
Any missing or altered event breaks chain continuity and should trigger investigation.

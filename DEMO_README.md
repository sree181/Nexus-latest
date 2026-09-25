# meshAgent Real-Data Client Demo

This release can demonstrate the complete **Developer → Analyst → CISO** workflow with a real local Git clone and a real Cursor session. meshAgent does not replace Cursor or GitHub. Cursor supplies opted-in coding events; the Git working tree supplies repository, remote, branch, and commit context; meshAgent governs the resulting package evidence, review, case, exception, remediation, and decision record.

## Start here

1. Follow [Mac Live Demo Setup](docs/demo/MAC_LIVE_DEMO_SETUP.md).
2. Run `scripts/demo/verify-live-demo.sh http://localhost:8080` before the meeting.
3. Follow [Live Client Demo Runbook](docs/demo/LIVE_CLIENT_DEMO_RUNBOOK.md) during rehearsal and delivery.
4. Keep [Presenter Checklist](docs/demo/PRESENTER_CHECKLIST.md) open for the final preflight and contingency path.
5. Use the [editable client-demo PowerPoint](docs/demo/MeshAgent_Client_Demo_Deck.pptx) for the opening narrative.
6. Use the [90-second storyboard](docs/demo/MESHAGENT_90_SECOND_MASTER_FILM_STORYBOARD.md), [voice-actor script](docs/demo/MESHAGENT_90_SECOND_NARRATION.txt), and [promotional cut scripts](docs/demo/meshAgent_PROMOTIONAL_CUTS_SCRIPT.md) as the approved film package.

## What is live

The recommended demonstration creates a new Cursor session during the meeting, records a real file change and package command, submits a real Developer review request, assigns and investigates it as an Analyst, requests a governed exception, decides it with a separate CISO identity, records remediation evidence, and returns to a clean package verification. The repository remote, branch, and commit shown by meshAgent come from the local Git checkout.

## What is not claimed

Version 1 does not ingest GitHub pull-request or issue webhooks. It does not provide a managed multi-tenant service, active-active availability, or external audit notarization. Local demo identity is visibly labelled and must be replaced by customer OpenID Connect before production acceptance.

## Demo support boundary

Use a disposable branch in an approved repository. Do not enter client secrets, regulated data, production credentials, or proprietary prompts that the client has not approved for the meeting. The preflight is read-only and does not create sample work.

For product and operational detail, see [README](README.md), [Production Operations](docs/PRODUCTION_OPERATIONS.md), and [Release Validation Report](RELEASE_VALIDATION_REPORT.md).

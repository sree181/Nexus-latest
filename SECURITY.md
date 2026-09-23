# Security Policy

## Reporting a vulnerability

**Do not report suspected vulnerabilities in a public issue, discussion, pull request, chat transcript, or package review.** Use the repository host's private security-advisory reporting feature when it is enabled. If it is unavailable, contact the organization that operates this repository through its established private security channel and include `MeshAgent security report` in the subject. If neither route is available, contact the deployment or support owner identified in [SUPPORT.md](SUPPORT.md) and request a private reporting channel before sharing technical details.

A useful report includes the affected release or commit, deployment mode, prerequisites, a minimal reproduction, impact assessment, logs or request/response metadata with secrets removed, and whether the issue may involve exposed customer data. Do **not** include access tokens, device tokens, private keys, recovery identities, full governed-memory content, source bodies, or production URLs that increase exposure.

We aim to acknowledge a private report within **five business days** and to provide a status update within **ten business days**. These are response targets rather than a promise of remediation timing. Triage, remediation, disclosure timing, and any customer communication depend on severity, reproducibility, affected deployments, legal obligations, and maintainer availability.

## Scope and handling

Reports are in scope when they plausibly affect the current MeshAgent production-v1 repository or its shipped adapters and operations scripts, including authentication or authorization bypass, device-token scope escape, sensitive data exposure, unsafe backup/restore behavior, supply-chain compromise, or denial of service with a credible impact path.

Deployment-specific ingress, identity-provider configuration, KMS or backup-key custody, host compromise, customer network rules, external model providers, and unsupported modifications may be outside the repository's remediation control. They may still warrant incident handling by the affected deployment owner. The maintainers will state the boundary rather than imply that a repository-only fix resolves a deployment issue.

Please allow reasonable time for validation and remediation before public disclosure. Do not access, modify, retain, or exfiltrate data beyond what is necessary to demonstrate an issue. Stop testing if you encounter customer data, service instability, or evidence that the impact exceeds the agreed scope.

## Security update process

After validating a report, maintainers will assess affected versions and configurations, develop and test a fix or mitigation, prepare release notes that avoid exposing users prematurely, and coordinate disclosure with affected operators where appropriate. Security fixes are recorded in [CHANGELOG.md](CHANGELOG.md) when disclosure is appropriate. A changelog entry does not assert that every deployment has installed the fix.

MeshAgent v1's audit log is hash chained but not externally signed, its container examples are local deployment aids rather than a complete production perimeter, and its backup encryption uses a deployment-provided hook. Reports about weaknesses in those boundaries are welcome; mitigations may require both repository and deployment changes.

## Supported versions

| Version line | Security-fix status |
|---|---|
| `0.1.x` / production v1 | Current repository line; fixes are considered subject to maintainer capacity and release process |
| Earlier prototypes and documents under `docs/archive/` | Not supported; archived and non-authoritative |

For environment-specific incident response, follow the [production operations runbook](docs/PRODUCTION_OPERATIONS.md#7-incident-response) and local incident procedures.

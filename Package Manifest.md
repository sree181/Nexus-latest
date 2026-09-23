# Package Manifest

This repository archive contains the complete portable **MeshAgent Security Workbench source code**, the dependency lockfile, build configuration, Cursor project rule, runtime contracts, production HTTP adapter, OpenAPI contract, and backend integration documentation.

## Included

The archive includes:

- All application source under `client/`, `server/`, and `shared/`
- React, TypeScript, Vite, and Tailwind configuration
- `package.json` and `pnpm-lock.yaml`
- The Wouter compatibility patch under `patches/`
- `README.md`
- `CURSOR_BACKEND_INTEGRATION.md`
- `CURSOR_HANDOFF_PROMPT.md`
- `API_CONTRACT.openapi.yaml`
- `INTEGRATION_GUIDE.md`
- `HYPERGRAPH_EXPLORER_INTEGRATION.md`
- `env.template.txt`
- `.cursor/rules/meshagent-backend-integration.mdc`

## Deliberately excluded

The archive excludes generated and machine-specific content:

- `node_modules/`
- `dist/`
- `.git/`
- `.manus-logs/`
- `.webdev/`
- `.project-config.json`
- Manus browser-debug files under `client/public/__manus__/`
- Real `.env` files and secrets

Dependencies are reproducibly restored with `pnpm install`. Production output is regenerated with `pnpm build`.

## Portability

The packaged source does not depend on Manus-hosted visual assets. The application’s ambient surface is implemented with CSS gradients. Google Fonts are loaded from their public stylesheet; teams that require fully offline operation can self-host the specified IBM Plex and JetBrains Mono families.

## Verification

Before packaging, the project was checked with:

```bash
pnpm check
pnpm build
```

The OpenAPI YAML was parsed through Prettier’s YAML parser. The ZIP was then listed and checked to confirm that excluded directories, generated output, and environment files were absent.

## References

[1]: https://pnpm.io/cli/install "pnpm install"
[2]: https://vite.dev/guide/build.html "Vite Production Build"
[3]: https://spec.openapis.org/oas/latest.html "OpenAPI Specification"

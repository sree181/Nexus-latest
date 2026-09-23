# MeshAgent — Product UI (production scaffold)

Governed agent memory over a HyperMesh hypergraph. This is a real, runnable
monorepo, not a mockup: a React + TypeScript frontend with a live Cytoscape
graph, a FastAPI backend, and a single gateway seam where the MeshAgent engine
plugs in. It runs today on curated sample data with no engine present.

## What is here

```
meshagent/
  apps/web/            React + Vite + TypeScript SPA (the product UI)
    src/features/graph/  the live graph renderer (Cytoscape today)
    src/routes/          screens (Start a task, Fleet hypergraph)
  packages/ui/         design system: tokens + accessible components
  packages/graph/      renderer-agnostic graph model (GraphPayload, graphology)
  services/api/        FastAPI: REST + WebSocket over the MeshAgent gateway
    app/gateway.py       THE seam: SampleGateway | EngineGateway (real HyperMesh)
    app/engine_gateway.py the real gateway; runs on live engine memory
    app/engine_seed.py   seeds real HyperMesh-backed memory (run + fleet stores)
    app/models.py        the wire contract (mirrors packages/graph/types.ts)
    app/hgviz/           structure-aware hypergraph analysis (see below)
    app/analysis.py      hgviz results -> governance signals (API models)
  services/engine/     the MeshAgent engine (meshagent + hypermeshdb + hypermesh_core)
  docker-compose.yml   self-hosted, single-tenant, runs in the customer network
```

The stack: React 18 + TypeScript, Vite, TanStack Query + Router, Tailwind + a
small owned component set, Cytoscape for graphs (Sigma/deck.gl/three are the
documented upgrade paths). FastAPI + Pydantic v2 in front of the Python MeshAgent
package, since HyperMesh is reached through a Python binding. Everything ships as
containers so it can run inside a customer's boundary. This is the CISO
requirement: their code and agent memory never leave their network.

## Prerequisites

- Node 20+ and pnpm 9 (`corepack enable` gives you pnpm without a global install)
- Python 3.12+

## Run it locally (two terminals)

Terminal 1 — the API:

```bash
cd services/api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Terminal 2 — the web app:

```bash
corepack enable
pnpm install
cp .env.example .env
pnpm dev:web
```

Open http://localhost:5173. The Vite dev server proxies `/api` to the API on
:8000, so the Fleet screen runs a real query against the backend and renders the
result with Cytoscape. Check the API directly at http://localhost:8000/api/health
and its docs at http://localhost:8000/docs.

### Run on the real engine

By default the API uses the sample gateway. To run on the real MeshAgent engine
(bundled under `services/engine`), build its compiled core once and start the
API in engine mode:

```bash
make -C services/engine/hypermesh_core        # builds libhypermesh.dylib / .so
cd services/api
MESHAGENT_ENGINE=1 PYTHONPATH=../engine uvicorn app.main:app --reload --port 8000
```

HyperMesh stores its hypergraph in a C core reached over ctypes, so without that
library the engine raises `EngineNotInstalledError` and only sample mode runs.
The repo ships the Linux `.so`; macOS needs the `make` above. Python 3.12 is the
supported interpreter (pydantic 2.9 has no wheels for 3.14 yet).

`/api/health` then reports `"gateway":"EngineGateway"`. The API seeds real
HyperMesh-backed memory (an agent's pickle.load build, and a multi-agent fleet
sharing the vulnerable numpy), and every structure endpoint is computed from
genuine engine records via `export_graph` and hyperedge reconstruction, not from
sample data. Set `MESHAGENT_DB_DIR` to persist the memory across restarts.
`docker compose up --build` runs in engine mode already.

## Run it with Docker (one command)

```bash
docker compose up --build
```

Web on http://localhost:8080, API on http://localhost:8000. nginx serves the
static bundle and proxies `/api` (including WebSockets) to the API container.

## Working in Cursor

This repo is set up for Cursor. A `.cursorrules` file at the root teaches Cursor's
AI the architecture and conventions, so its edits respect the gateway seam, the
mirrored wire contract, and the design tokens.

1. Open the folder: `cursor .` from the repo root (or File > Open Folder). Open the
   whole monorepo, not a subfolder, so Cursor indexes the API and the frontend
   together and can reason across the wire contract.
2. Recommended extensions (Cursor will suggest them): ESLint, Prettier, Tailwind
   CSS IntelliSense, Python (Pylance). Point Pylance at `services/api/.venv`.
3. Two run configs, two terminals, as above. Keep both running while you work;
   Vite and uvicorn both hot-reload.
4. Use Cursor's Composer (Cmd/Ctrl+I) for multi-file changes and Chat (Cmd/Ctrl+L)
   for questions. Good prompts for this repo:
   - "Add a Recommendations screen: extend the gateway, the API route, the fetcher,
     and a new route. Follow the steps in .cursorrules."
   - "Generate a typed API client from the FastAPI /openapi.json and replace the
     hand-written fetchers in apps/web/src/lib/api.ts."
   - "Add the isometric planes view for a single run using react-three-fiber,
     consuming a GraphPayload from /api/runs/{id}/graph."
   Add files to the prompt context with @ (for example `@gateway.py @api.ts`) so
   Cursor edits the right seam.
5. Before committing, run `pnpm typecheck` (web) and keep TypeScript strict-clean.

## The structure-aware hypergraph layer (hgviz)

`services/api/app/hgviz/` implements the topological method of Oliver, Zhang &
Zhang, *Structure-Aware Simplification for Hypergraph Visualization* (IEEE TVCG
2024, arXiv:2407.19621), over MeshAgent's governed memory hypergraph, and
`app/analysis.py` turns its output into governance signals:

- **Bipartite (Koenig) representation** of the memory hypergraph (`hypergraph.py`).
- **Topological decomposition** into blocks / bridges / branches via biconnected
  components (`decompose.py`).
- **Entanglement index** eta = B1/|V| per block: a coupling score. The exploitable
  pickle.load knot comes out as the one entangled block; the fleet numpy exposure
  as a denser block containing a **forbidden cluster** (unavoidable coupling).
- **Forbidden sub-hypergraph** detection (`forbidden.py`): the configurations that
  force unavoidable overlaps, i.e. genuine tight coupling.
- **Structure-aware simplification** (`simplify.py`): leaf pruning + minimal cycle
  collapse for the multi-scale view (the scale slider on the Structure screen).

Governance reinterpretation: blocks = coupled risk with a score, bridges = single
points of propagation (cut for maximum decoupling), branches = peripheral,
forbidden clusters = coupling you cannot design away. The **Structure** screen
(`/structure`) renders this with the polygon metaphor (`PolygonHypergraph.tsx`,
d3-force layout + convex-hull polygons colored by structure and entanglement) and
the scale slider.

Endpoints: `/api/runs/{id}/hypergraph`, `/api/runs/{id}/decomposition`,
`/api/runs/{id}/scales`, `/api/fleet/decomposition`.

Tests: `cd services/api && pip install -r requirements-dev.txt && pytest`. The
engine is covered by unit tests on hypergraphs with known topology
(`tests/test_hgviz.py`): Betti numbers, block/bridge/branch classification,
entanglement values, forbidden bundles, and that simplification changes B1 exactly
as the theory predicts.

Honesty note: the paper renders with Qu et al.'s primal-dual polygon *optimizer*
(near-regular polygons). This scaffold uses a force layout plus convex-hull
polygons, a lighter substitution that carries the metaphor. The analysis (the
differentiated part) is faithful; the strangled-vertex/hyperedge forbidden
variants are surfaced through per-block entanglement rather than the full
cycle-adjacency enumeration. Both are noted in the code.

## The engine wiring (done)

The engine is merged into this repo under `services/engine` (the `meshagent`,
`hypermeshdb` and `hypermesh_core` packages, pure Python here). `EngineGateway`
(`services/api/app/engine_gateway.py`) implements the same `Gateway` interface as
the sample one, but every method reads real HyperMesh memory:

- `run_graph` comes from the engine's own `export_graph`.
- the structure endpoints reconstruct the real hyperedges (each n-ary memory
  record is one hyperedge) and analyze them with `hgviz`.
- the fleet endpoints read genuine multi-agent memory.

`get_gateway()` returns the `EngineGateway` when `MESHAGENT_ENGINE=1`, else the
`SampleGateway`. Nothing in the UI changes between the two, because both return
the same Pydantic models. That is the point of the seam.

In production you swap the pure-Python engine here for your built HyperMesh wheel
and point `MESHAGENT_DB_DIR` at its data directory; the gateway code is unchanged.
Engine-mode tests live in `tests/test_engine_gateway.py` (skipped when the engine
is absent) and assert the decomposition of the real seeded memory.

## What to build next (honest backlog)

- The isometric planes view is the one screen that needs real 3D
  (react-three-fiber). It is scaffolded in the design, not yet in code here.
- Auth: OIDC/SAML against the customer IdP; identity keys the per-developer
  memory scope. Enforce scopes at the API.
- Move the two SQLite sidecars (registry, content) to Postgres for concurrent
  multi-agent writes at fleet scale. The hypergraph itself stays in HyperMesh.
- Generate the TS client from OpenAPI so the wire contract has one source.
- OpenTelemetry traces and structured logs for the customer SOC.

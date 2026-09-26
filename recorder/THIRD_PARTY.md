# Stage 1A native recorder — direct dependencies

| Module | Version | Purpose | License |
|---|---:|---|---|
| `github.com/Microsoft/go-winio` | `v0.6.2` | Current-user Windows named pipe | MIT |
| `golang.org/x/sys` | `v0.44.0` | Linux/macOS peer credentials and Windows file locking | BSD-3-Clause |
| `modernc.org/sqlite` | `v1.29.10` | Pure-Go durable SQLite queue | BSD-3-Clause |

The versions above are pinned by `go.mod`/`go.sum`. Transitive dependencies are included in the repository SBOM and vulnerability scan. Binary redistribution must retain the applicable notices from each dependency's license file.

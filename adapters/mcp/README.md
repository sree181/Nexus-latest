# MeshAgent MCP server

Seven tools: three for an agent to say *why* and ask before it installs
something, four for it to read governed memory back. Works with any
MCP-capable client — Cursor, Claude Code, Copilot.

Standard library only, stdio JSON-RPC.

## Why this is the second half and not the first

The editor hooks record what happened. They cannot record why: a hook sees a
file being written and never the reasoning behind it, so everything it sends
arrives explicitly unexplained. That is the honest outcome, and it is also
the number on the Fleet screen saying most of the estate is unaccounted for.

This layer lets an agent close that gap voluntarily. An agent that never
calls it produces a perfect, empty record — which is exactly why the hooks
exist underneath, and why coverage is measured at all.

## Install

```jsonc
// Cursor: .cursor/mcp.json — Claude Code: .mcp.json
{
  "mcpServers": {
    "meshagent": {
      "command": "python3",
      "args": ["/abs/path/to/meshagent-app/adapters/mcp/meshagent_mcp.py"],
      "env": { "MESHAGENT_API": "http://localhost:8000" }
    }
  }
}
```

**That path has to be absolute.** This file lives in the repository you are
recording, and the editor launches the server with that repository as its
working directory — not this checkout. A relative path resolves to a file that
is not there, and the server simply never starts: the tools are absent, the
agent has nothing to volunteer a reason through, and nothing says why. The same
applies to the hook commands under `adapters/cursor` and
`adapters/claude-code`.

MeshAgent's **Connect your editor** screen generates this block with the path
already filled in, as the API process sees it.

It uses the same credential as the hooks, so `meshagent login` covers both.

## The tools

Writing — the agent volunteering something:

| Tool | What it does |
| --- | --- |
| `record_decision` | States why, and **names the files it covers** |
| `check_package` | Asks the gate before installing a dependency |
| `session_status` | Whether any of this is actually attaching to anything |

Reading — the agent consulting memory it did not write:

| Tool | What it does |
| --- | --- |
| `why` | The evidence chain behind a class or module, nearest first |
| `rewind` | What the run held at a past moment |
| `findings` | Dangerous call sites, and whether anyone established reachability |
| `sbom` | Dependencies, licences and advisories |

The read tools default to the run this editor session is being recorded
into, so an agent asking "why does this exist" gets the code in front of it
without having to know a run id. Pass `run` to ask about another.

They are scoped the way the UI is: a developer reaches their own runs, and
nothing else. A refusal comes back as readable text, because being told no
is a normal answer here rather than a failure.

## `forget` is deliberately absent

The fourth verb has no tool. A model that can destroy governed memory
unprompted makes the deletion certificate worthless, and the preview-then-
confirm flow the UI uses assumes a human read the preview. Deleting memory
stays a decision a person makes.

## The one design decision worth reading

`record_decision` takes the modules it covers, named by the agent.

The tempting shortcut is to attach the most recent decision to the next file
written — the hook and this server are different processes, milliseconds
apart, and the correlation would usually be right. It would also be a guess
wearing a provenance record's clothing, which is the worst failure available
to a product whose whole claim is that you can read why a line of code
exists.

So a file is explained because the agent said so, never because of when it
was written. An agent that records a reason and names nothing gets a reply
telling it so, and its code stays exactly as unexplained as before.

## `unknown` is not `allow`

`check_package` returns one of four verdicts, and the tool result spells out
what `unknown` means rather than leaving the model to infer it from a word it
has not seen before. An agent reading `unknown` as "no problems found" is the
failure this wording exists to prevent.

The read tools carry the same rule, because the reader is a model and the
gap between "we looked and found nothing" and "nobody looked" does not
survive terse phrasing:

- `findings` says **NOT ASSESSED — do not read this as safe** rather than
  leaving a blank reachability field to be taken as clean, and reports an
  unscanned run as unscanned instead of as having no findings.
- `sbom` marks advisories that came from curated sample data, or from no
  feed at all, so an empty CVE list is not mistaken for a clean bill.
- `rewind` reports memory deleted since as **DELETED**, naming it and
  refusing to reproduce its content. An agent describing an erased memory as
  one that never existed would turn a deletion into a lie; one that could
  recite it would repeal the certificate.
- `why` calls out a chain that bottoms out in unverified external input,
  rather than trusting a model to notice two words in a bracket.

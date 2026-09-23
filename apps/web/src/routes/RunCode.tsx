import { useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import type { CodeModule } from "../lib/api";
import { api } from "../lib/api";
import { Async } from "../components/Async";

/** One module, verbatim. The line numbers are the reader's, not the engine's:
 *  they are there to talk about a call site, not because memory holds them. */
function Module({ module }: { module: CodeModule }) {
  const lines = module.code.replace(/\n$/, "").split("\n");

  return (
    <section
      aria-label={`Source of ${module.name}`}
      className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-[15px] text-ink">{module.name}</h2>
        <span className="font-mono text-[11px] text-slate">
          {lines.length} line{lines.length === 1 ? "" : "s"}
          {module.classes.length > 0 && ` · ${module.classes.join(", ")}`}
        </span>
      </div>
      <pre className="overflow-auto rounded-xl border border-line-2 bg-surface-2 p-4">
        <code className="block font-mono text-[12.5px] leading-relaxed text-ink">
          {lines.map((line, i) => (
            <span key={i} className="flex">
              <span
                aria-hidden="true"
                className="mr-4 w-8 shrink-0 select-none text-right text-slate"
              >
                {i + 1}
              </span>
              <span className="whitespace-pre">{line}</span>
            </span>
          ))}
        </code>
      </pre>
    </section>
  );
}

export function RunCode() {
  const { runId } = useParams({ from: "/runs/$runId" });

  const code = useQuery({
    queryKey: ["run", runId, "code"],
    queryFn: () => api.runCode(runId),
  });

  return (
    <Async
      query={code}
      label="reading the source out of memory…"
      isEmpty={(d) => d.modules.length === 0}
      emptyTitle="No code recorded"
      emptyDetail="This run wrote no module into memory, so there is no source to read. The classes and findings on the other screens come from code, so they are empty too."
    >
      {(d) => (
        <div className="flex flex-1 flex-col gap-5 overflow-auto p-6">
          <p className="font-mono text-[11.5px] text-slate">
            {d.sample
              ? "Sample source, carried with the curated run."
              : "Exactly what the agent submitted, read back out of governed memory."}
          </p>
          {d.modules.map((m) => (
            <Module key={m.name} module={m} />
          ))}
        </div>
      )}
    </Async>
  );
}

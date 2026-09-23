import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import { api } from "../lib/api";

/** Says on every screen when what this deployment is sent does not survive.
 *
 *  Both conditions it reports are silent from the outside. Sample mode answers
 *  a recorder post with a success receipt for a batch it discarded, so an
 *  adapter author is told their recording worked when it was thrown away. An
 *  ephemeral store loses every device token and every certificate on the next
 *  restart, and nothing in a response says so.
 *
 *  There is deliberately no dismiss control. Hiding this would return the app
 *  to the state it is here to fix — reporting a write that did not happen. */
export function ModeBanner() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    staleTime: Infinity,
    retry: false,
  });

  const got = health.data;
  if (!got) return null;
  const discarding = !got.mode.persists;
  if (!discarding && got.durable) return null;

  return (
    <div
      role="status"
      className={`flex shrink-0 items-start gap-3 border-b px-6 py-2.5 ${
        discarding ? "border-risk bg-risk-soft" : "border-warn bg-warn-soft"
      }`}
    >
      <Badge tone={discarding ? "risk" : "warn"}>
        {discarding ? "not recording" : "not durable"}
      </Badge>
      <div className="flex flex-col gap-1">
        {/* verbatim: the deployment says what it does with a write, and this
            is not the place to paraphrase it */}
        <p className="text-[12.5px] leading-snug text-ink">{got.mode.note}</p>
        {!got.durable && (
          <p className="text-[12.5px] leading-snug text-ink">
            No memory directory is configured either, so governed memory,
            registered devices and the action log live in a temporary
            directory and go when this API process does. Set{" "}
            <code className="font-mono text-[12px]">MESHAGENT_DB_DIR</code> to
            keep them.
          </p>
        )}
      </div>
    </div>
  );
}

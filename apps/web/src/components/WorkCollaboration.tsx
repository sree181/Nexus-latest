import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type WorkKind } from "../lib/api";
import { timestamp } from "../lib/format";
import { Field, MutationMessage, TextArea, TextInput } from "./WorkflowUI";

export function WorkCollaboration({ kind, id }: { kind: WorkKind; id: string }) {
  const client = useQueryClient();
  const [success, setSuccess] = useState<string | null>(null);
  const comments = useQuery({
    queryKey: ["work-comments", kind, id],
    queryFn: () => api.workComments(kind, id),
    refetchInterval: 15_000,
  });
  const add = useMutation({
    mutationFn: (input: { message: string; mentions: string[] }) =>
      api.addWorkComment(kind, id, input),
    onSuccess: () => {
      setSuccess("Note shared.");
      void client.invalidateQueries({ queryKey: ["work-comments", kind, id] });
      void client.invalidateQueries({ queryKey: ["work-activity"] });
      void client.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-lg font-semibold text-ink">Team notes</h2>
          <p className="mt-1 text-sm text-slate">Keep investigation context with the work item. Mention teammates by email when they need to respond.</p>
        </div>
        <span className="font-mono text-xs text-slate">{comments.data?.length ?? 0} notes</span>
      </div>
      {comments.isPending ? <p className="mt-4 text-sm text-slate">Loading notes…</p> : comments.isError ? <p role="alert" className="mt-4 text-sm text-risk">Notes could not be loaded.</p> : comments.data?.length ? (
        <ol className="mt-4 space-y-3">
          {comments.data.map((comment) => (
            <li key={comment.id} className="rounded-xl border border-line-2 bg-surface-2 px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <strong className="text-sm text-ink">{comment.actor_name}</strong>
                <span className="font-mono text-[11px] text-slate">{comment.actor_role} · {timestamp(comment.created_at)}</span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink">{comment.message}</p>
              {comment.mentions.length ? <p className="mt-2 text-xs text-accent">Mentioned: {comment.mentions.join(", ")}</p> : null}
            </li>
          ))}
        </ol>
      ) : <p className="mt-4 rounded-xl border border-dashed border-line-2 px-4 py-3 text-sm text-slate">No notes yet.</p>}
      <form className="mt-5 grid gap-3" onSubmit={async (event) => {
        event.preventDefault();
        setSuccess(null);
        const formElement = event.currentTarget;
        const form = new FormData(formElement);
        const mentions = String(form.get("mentions") ?? "").split(/[\s,]+/).map((value) => value.trim()).filter(Boolean);
        try {
          await add.mutateAsync({ message: String(form.get("message") ?? "").trim(), mentions });
          formElement.reset();
        } catch {
          // MutationMessage keeps the error visible and the note available to retry.
        }
      }}>
        <Field label="Add a note"><TextArea name="message" required maxLength={4096} placeholder="What did you confirm, and what should happen next?" /></Field>
        <Field label="Notify people" hint="Optional email addresses, separated by commas."><TextInput name="mentions" placeholder="alex@example.com" /></Field>
        <div className="flex items-center gap-3">
          <button type="submit" disabled={add.isPending} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{add.isPending ? "Sharing…" : "Share note"}</button>
        </div>
        <MutationMessage error={add.error} success={success} />
      </form>
    </section>
  );
}

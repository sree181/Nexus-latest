import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { ApiError } from "../lib/api";
import { StateFrame } from "./WorkflowVisual";

export function Loading({ label }: { label: string }) {
  return <StateFrame kind="loading" title={label} />;
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <StateFrame kind="empty" title={title} detail={detail} />;
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "An unexpected error occurred.";
  return <StateFrame kind="error" title="This information could not be loaded" detail={message} action={retry ? <Button variant="ghost" onClick={retry}>Try again</Button> : undefined} />;
}

/** Loading, empty, error and success for one query, in one place.
 *
 * A 404 from the API is not a failure: it means the record holds no evidence of
 * that kind yet, so it renders as an empty state with the API explanation. */
export function Async<T>({
  query,
  label,
  emptyTitle = "Nothing recorded yet",
  isEmpty,
  emptyDetail,
  children,
}: {
  query: UseQueryResult<T>;
  label: string;
  emptyTitle?: string;
  isEmpty?: (data: T) => boolean;
  emptyDetail?: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <Loading label={label} />;
  if (query.isError) {
    const err = query.error;
    if (err instanceof ApiError && err.status === 404) return <EmptyState title={emptyTitle} detail={err.message} />;
    return <ErrorState error={err} retry={() => void query.refetch()} />;
  }
  if (isEmpty?.(query.data)) return <EmptyState title={emptyTitle} detail={emptyDetail ?? "There is nothing here to show."} />;
  return <>{children(query.data)}</>;
}

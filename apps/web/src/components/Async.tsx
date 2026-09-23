import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";
import { ApiError } from "../lib/api";

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="grid flex-1 place-items-center p-8">
      <div className="flex max-w-[460px] flex-col items-center gap-3 text-center">
        {children}
      </div>
    </div>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <Frame>
      <p role="status" className="font-mono text-sm text-slate">
        {label}
      </p>
    </Frame>
  );
}

export function EmptyState({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <Frame>
      <p className="font-serif text-[19px] text-ink">{title}</p>
      <p className="text-[13.5px] leading-snug text-slate">{detail}</p>
    </Frame>
  );
}

export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  const message = error instanceof Error ? error.message : "unknown error";
  return (
    <Frame>
      <p className="font-serif text-[19px] text-risk">Could not load this</p>
      <p className="font-mono text-[12.5px] leading-snug text-slate">
        {message}
      </p>
      {retry && (
        <Button variant="ghost" onClick={retry}>
          Try again
        </Button>
      )}
    </Frame>
  );
}

/** Loading, empty, error and success for one query, in one place.
 *
 *  A 404 from the API is not a failure: it means the run holds no memory of
 *  that kind yet, so it renders as an empty state with the API's own
 *  explanation rather than as an error. */
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
    if (err instanceof ApiError && err.status === 404) {
      return <EmptyState title={emptyTitle} detail={err.message} />;
    }
    return <ErrorState error={err} retry={() => void query.refetch()} />;
  }
  if (isEmpty?.(query.data)) {
    return (
      <EmptyState
        title={emptyTitle}
        detail={emptyDetail ?? "There is nothing here to show."}
      />
    );
  }
  return <>{children(query.data)}</>;
}

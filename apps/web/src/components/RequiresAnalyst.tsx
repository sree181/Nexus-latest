import type { ReactNode } from "react";

import { useIdentity } from "../lib/useIdentity";

/** Guards the views that read across every developer's memory.
 *
 *  The API refuses these to a developer regardless — this is not the
 *  security boundary, and is not pretending to be one. It exists so that
 *  someone who follows a link into the fleet gets told why it is not theirs,
 *  instead of a screen full of failed requests.
 */
export function RequiresAnalyst({ children }: { children: ReactNode }) {
  const { analyst, loading } = useIdentity();

  if (loading) {
    return (
      <p role="status" className="p-8 font-mono text-sm text-ink-dim">
        checking who you are…
      </p>
    );
  }
  if (!analyst) {
    return (
      <div className="max-w-xl p-8">
        <h1 className="font-serif text-xl font-semibold text-ink">
          This view belongs to the security office
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-dim">
          The fleet views read across every developer&rsquo;s governed memory,
          which is exactly what one developer should not see of another. Your
          own runs, and everything recorded against them, are in your
          workspace.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}

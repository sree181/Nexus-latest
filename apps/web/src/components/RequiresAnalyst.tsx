import type { ReactNode } from "react";

import { RequiresCapability } from "./RequiresCapability";

/** Guards the views that read across every developer's memory.
 *
 *  The API refuses these to a developer regardless — this is not the
 *  security boundary, and is not pretending to be one. It exists so that
 *  someone who follows a link into the fleet gets told why it is not theirs,
 *  instead of a screen full of failed requests.
 */
export function RequiresAnalyst({ children }: { children: ReactNode }) {
  return (
    <RequiresCapability
      capability="fleet.read"
      title="This view belongs to the security organization"
      detail="Fleet views read across developers' governed memory. Use your own run workspace unless your server-managed role grants fleet access."
    >
      {children}
    </RequiresCapability>
  );
}

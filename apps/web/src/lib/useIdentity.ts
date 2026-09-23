import { useQuery } from "@tanstack/react-query";

import { api, type Capability, type Me } from "./api";

/** Who the API says you are.
 *
 *  Deliberately the server's answer, not the token's. A role decoded in the
 *  browser decides what this app draws; the role the API reads off the
 *  signed claims decides what it will actually serve. Asking the API keeps
 *  those two from drifting, so a screen can never offer something the
 *  server will refuse.
 */
export function useIdentity() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    staleTime: Infinity,
    retry: false,
  });
  return {
    me: me.data,
    loading: me.isPending,
    failed: me.isError,
    /** The security office. False while still loading: nothing is shown on
     *  the assumption of a role we have not confirmed. */
    analyst: me.data?.role === "analyst" || me.data?.role === "ciso",
    developer: me.data?.role === "developer",
    ciso: me.data?.role === "ciso",
    hasCapability: (capability: Capability) =>
      me.data?.capabilities.includes(capability) ?? false,
  };
}

export type { Me };

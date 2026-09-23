/** Entity names on the wire carry their kind as a prefix (`class:Loader`).
 *  Screens show the readable half and let the kind be a label of its own. */
export function entityLabel(entity: string | null | undefined): string {
  if (!entity) return "—";
  const at = entity.indexOf(":");
  return at === -1 ? entity : entity.slice(at + 1);
}

export function entityKind(entity: string | null | undefined): string {
  if (!entity) return "";
  const at = entity.indexOf(":");
  return at === -1 ? "" : entity.slice(0, at);
}

/** UTC, always. A deletion certificate is an audit artifact; it should not
 *  read differently depending on who opens it. */
export function timestamp(epochSeconds: number): string {
  return `${new Date(epochSeconds * 1000)
    .toISOString()
    .replace("T", " ")
    .slice(0, 19)} UTC`;
}

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

export function timestampMs(epochMilliseconds: number): string {
  return `${new Date(epochMilliseconds)
    .toISOString()
    .replace("T", " ")
    .slice(0, 19)} UTC`;
}

export function relativeTimeMs(epochMilliseconds: number, now = Date.now()): string {
  const seconds = Math.max(0, Math.floor((now - epochMilliseconds) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

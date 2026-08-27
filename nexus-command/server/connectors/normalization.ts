import { createHash } from 'node:crypto';

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => [key, canonicalize(item)]),
    );
  }
  return value;
}

export function canonicalHash(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(canonicalize(value))).digest('hex');
}

export function asFiniteNumber(value: unknown): number | null {
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function asIsoDate(value: unknown, fallback = new Date()): string {
  const numeric = asFiniteNumber(value);
  const candidate = numeric !== null ? new Date(numeric) : new Date(String(value ?? ''));
  return Number.isNaN(candidate.getTime()) ? fallback.toISOString() : candidate.toISOString();
}

export function qualityFlagsForAge(observedAt: string, staleAfterSeconds: number, now = new Date()): string[] {
  const ageSeconds = Math.max(0, (now.getTime() - new Date(observedAt).getTime()) / 1000);
  if (ageSeconds > staleAfterSeconds * 2) return ['stale', 'outside_operational_freshness'];
  if (ageSeconds > staleAfterSeconds) return ['delayed'];
  return [];
}

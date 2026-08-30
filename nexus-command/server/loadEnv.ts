import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** Loads a local `.env` in development. Production uses platform variables only. */
export function loadLocalEnv(env: NodeJS.ProcessEnv = process.env): void {
  if (env.NODE_ENV === 'production') return;
  const path = resolve(process.cwd(), '.env');
  if (!existsSync(path)) return;

  for (const raw of readFileSync(path, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (env[key] === undefined) env[key] = value;
  }
}

loadLocalEnv();

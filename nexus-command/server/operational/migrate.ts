import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { closeDatabasePool, getDatabasePool } from './database.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const migrationsDirectory = path.join(__dirname, 'migrations');

async function migrate(): Promise<void> {
  const pool = getDatabasePool();
  const client = await pool.connect();

  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id text PRIMARY KEY,
        applied_at timestamptz NOT NULL DEFAULT now()
      )
    `);

    const migrations = (await readdir(migrationsDirectory))
      .filter(file => file.endsWith('.sql'))
      .sort();

    for (const migration of migrations) {
      const existing = await client.query(
        'SELECT migration_id FROM schema_migrations WHERE migration_id = $1',
        [migration],
      );

      if (existing.rowCount) {
        console.log(`[migrate] ${migration} already applied`);
        continue;
      }

      const sql = await readFile(path.join(migrationsDirectory, migration), 'utf8');
      await client.query('BEGIN');
      try {
        await client.query(sql);
        await client.query('INSERT INTO schema_migrations (migration_id) VALUES ($1)', [migration]);
        await client.query('COMMIT');
        console.log(`[migrate] applied ${migration}`);
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      }
    }
  } finally {
    client.release();
    await closeDatabasePool();
  }
}

migrate().catch(error => {
  console.error('[migrate] failed', error);
  process.exitCode = 1;
});

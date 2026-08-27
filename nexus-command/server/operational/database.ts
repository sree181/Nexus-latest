import pg from 'pg';

const { Pool } = pg;

export type DatabasePool = pg.Pool;
export type DatabaseClient = pg.PoolClient;

let pool: pg.Pool | null = null;

export function hasDatabaseConfiguration(): boolean {
  return Boolean(process.env.DATABASE_URL);
}

function postgresSslOptions(): { rejectUnauthorized: boolean } | undefined {
  if (process.env.PGSSL !== 'true') return undefined;

  if (process.env.PGSSL_REJECT_UNAUTHORIZED === 'false') return { rejectUnauthorized: false };
  if (process.env.PGSSL_REJECT_UNAUTHORIZED === 'true') return { rejectUnauthorized: true };

  // Railway Postgres terminates TLS with a platform certificate that Node
  // reports as SELF_SIGNED_CERT_IN_CHAIN. Keep encryption; skip CA verification.
  if (process.env.RAILWAY_ENVIRONMENT) return { rejectUnauthorized: false };

  return { rejectUnauthorized: true };
}

export function getDatabasePool(): pg.Pool {
  if (!process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL is required for persistent Nexus operational workflows');
  }

  if (!pool) {
    pool = new Pool({
      connectionString: process.env.DATABASE_URL,
      max: Number(process.env.DB_POOL_MAX || 10),
      idleTimeoutMillis: Number(process.env.DB_IDLE_TIMEOUT_MS || 30_000),
      connectionTimeoutMillis: Number(process.env.DB_CONNECT_TIMEOUT_MS || 5_000),
      ssl: postgresSslOptions(),
      application_name: 'nexus-coordinate',
    });

    pool.on('error', error => {
      console.error('[database] Unexpected idle client error', error);
    });
  }

  return pool;
}

export async function withTransaction<T>(operation: (client: pg.PoolClient) => Promise<T>): Promise<T> {
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    const result = await operation(client);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}

export async function closeDatabasePool(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

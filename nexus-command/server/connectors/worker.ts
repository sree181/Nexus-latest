/**
 * Dedicated connector-worker entrypoint for platforms where the start command can be overridden.
 * On a platform that always runs the image default, set `NEXUS_SERVICE_ROLE=connector-worker`
 * instead and `server/index.ts` will run this same loop.
 */
import '../loadEnv.js';
import { hasDatabaseConfiguration } from '../operational/database.js';
import { startConnectorWorker } from './workerLoop.js';

if (!hasDatabaseConfiguration()) {
  throw new Error('DATABASE_URL is required for the Nexus connector worker');
}

const worker = startConnectorWorker();

async function shutdown(signal: string): Promise<void> {
  console.info(`[connector-worker] Received ${signal}; stopping after the current ingestion cycle`);
  await worker.stop();
}

process.on('SIGTERM', () => { void shutdown('SIGTERM'); });
process.on('SIGINT', () => { void shutdown('SIGINT'); });

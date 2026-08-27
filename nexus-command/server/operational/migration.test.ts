import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { PGlite } from '@electric-sql/pglite';
import { afterEach, describe, expect, it } from 'vitest';

let database: PGlite | null = null;

afterEach(async () => {
  if (database) await database.close();
  database = null;
});

describe('operational, connector, and temporal graph migrations', () => {
  async function migrationSql(): Promise<string> {
    const files = ['001_operational_foundation.sql', '002_authoritative_connectors.sql', '003_temporal_operational_graph.sql'];
    const migrations = await Promise.all(files.map(file => readFile(
      path.resolve(process.cwd(), 'server/operational/migrations', file),
      'utf8',
    )));
    return migrations.join('\n').replace(/CREATE EXTENSION IF NOT EXISTS pgcrypto;?/i, '');
  }

  it('applies to PostgreSQL and creates the complete operational and connector domains', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());

    const requiredTables = [
      'agencies', 'principals', 'principal_roles', 'operational_events', 'sources',
      'evidence_events', 'incidents', 'agent_findings', 'recommendations', 'recommendation_evidence',
      'approval_requirements', 'decisions', 'commitments', 'commitment_transitions',
      'execution_confirmations', 'idempotency_records', 'audit_events', 'outbox_events',
      'connector_runs', 'connector_checkpoints',
      'graph_ingestion_batches', 'graph_nodes', 'graph_edges', 'graph_node_evidence',
      'graph_edge_evidence', 'graph_state_changes',
    ];
    const result = await database.query<{ table_name: string }>(
      `SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ANY($1::text[])`,
      [requiredTables],
    );
    expect(result.rows.map(row => row.table_name).sort()).toEqual(requiredTables.sort());
  });

  it('creates connector provenance, health, and idempotent run-claim fields', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());
    const columns = await database.query<{ table_name: string; column_name: string }>(
      `SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public'
          AND ((table_name = 'sources' AND column_name IN ('authority_uri', 'connector_code', 'connection_status', 'last_attempt_at'))
            OR (table_name = 'evidence_events' AND column_name IN ('provenance', 'content_hash', 'source_event_id')))
        ORDER BY table_name, column_name`,
    );
    expect(columns.rows).toHaveLength(7);
  });

  it('rejects cross-mode incident references at the database boundary', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());

    const agencyId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
    const eventId = '22222222-2222-4222-8222-222222222222';
    await database.query(
      `INSERT INTO agencies (agency_id, code, name, agency_type) VALUES ($1, 'command', 'Event Command', 'command')`,
      [agencyId],
    );
    await database.query(
      `INSERT INTO operational_events (
         event_id, mode, event_type, name, phase, status, starts_at, location_name, command_owner_agency_id
       ) VALUES ($1, 'live', 'sec_gameday', 'SEC Game Day', 'arrival', 'active', now(), 'Auburn', $2)`,
      [eventId, agencyId],
    );

    await expect(database.query(
      `INSERT INTO incidents (
         event_id, mode, title, what_changed, why_it_matters, severity, status, detected_at
       ) VALUES ($1, 'training', 'Mode mismatch', 'Test', 'Test', 'high', 'active', now())`,
      [eventId],
    )).rejects.toThrow(/mode does not match/i);
  });

  it('versions graph state, records append-only history, and rejects cross-mode graph writes', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());
    const agencyId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
    const eventId = '22222222-2222-4222-8222-222222222222';
    await database.query(
      `INSERT INTO agencies (agency_id, code, name, agency_type) VALUES ($1, 'command', 'Event Command', 'command')`,
      [agencyId],
    );
    await database.query(
      `INSERT INTO operational_events (event_id, mode, event_type, name, phase, status, starts_at, location_name, command_owner_agency_id)
       VALUES ($1, 'live', 'sec_gameday', 'SEC Game Day', 'arrival', 'active', now(), 'Auburn', $2)`,
      [eventId, agencyId],
    );
    const first = await database.query<{ graph_node_id: string }>(
      `INSERT INTO graph_nodes (event_id, mode, node_type, external_key, label, current_state, state_hash, valid_from)
       VALUES ($1, 'live', 'parking_lot', 'lot-west', 'West Campus Lot', '{"occupancy":90}'::jsonb, 'hash-1', now())
       RETURNING graph_node_id`,
      [eventId],
    );
    const second = await database.query<{ graph_node_id: string }>(
      `INSERT INTO graph_nodes (event_id, mode, node_type, external_key, label, current_state, state_hash, valid_from)
       VALUES ($1, 'live', 'road_segment', 'wire-road', 'Wire Road', '{"speedMph":12}'::jsonb, 'hash-2', now())
       RETURNING graph_node_id`,
      [eventId],
    );
    await database.query(
      `INSERT INTO graph_edges (event_id, mode, edge_type, external_key, from_node_id, to_node_id, current_state, state_hash, valid_from)
       VALUES ($1, 'live', 'feeds_traffic_into', 'west-to-wire', $2, $3, '{"active":true}'::jsonb, 'edge-1', now())`,
      [eventId, first.rows[0].graph_node_id, second.rows[0].graph_node_id],
    );
    const updated = await database.query<{ version: number }>(
      `UPDATE graph_nodes SET current_state='{"occupancy":95}'::jsonb, state_hash='hash-3'
       WHERE graph_node_id=$1 RETURNING version`,
      [first.rows[0].graph_node_id],
    );
    expect(updated.rows[0].version).toBe(2);
    const history = await database.query<{ count: string }>(
      `SELECT count(*)::text AS count FROM graph_state_changes WHERE entity_kind='node' AND entity_id=$1`,
      [first.rows[0].graph_node_id],
    );
    expect(Number(history.rows[0].count)).toBe(2);
    await expect(database.query(
      `UPDATE graph_state_changes SET new_state='{}'::jsonb WHERE entity_kind='node' AND entity_id=$1`,
      [first.rows[0].graph_node_id],
    )).rejects.toThrow(/append-only/i);
    await expect(database.query(
      `INSERT INTO graph_nodes (event_id, mode, node_type, external_key, label, state_hash, valid_from)
       VALUES ($1, 'training', 'parking_lot', 'bad-mode', 'Wrong Mode', 'bad', now())`,
      [eventId],
    )).rejects.toThrow(/mode does not match/i);
  });
});

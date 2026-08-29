import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { PGlite } from '@electric-sql/pglite';
import { afterEach, describe, expect, it } from 'vitest';

let database: PGlite | null = null;

// PGlite compiles and boots a WebAssembly Postgres per test, which routinely exceeds the
// default 5s budget on a cold cache.
const MIGRATION_TIMEOUT_MS = 60_000;

let cachedSql: string | null = null;

afterEach(async () => {
  if (database) await database.close();
  database = null;
});

describe('operational, connector, and temporal graph migrations', () => {
  async function migrationSql(): Promise<string> {
    if (cachedSql) return cachedSql;
    const files = [
      '001_operational_foundation.sql',
      '002_authoritative_connectors.sql',
      '003_temporal_operational_graph.sql',
      '004_live_command_window.sql',
      '005_scenario_packs_and_detection.sql',
      '006_public_hazard_sources.sql',
      '007_agent_desk_findings.sql',
    ];
    const migrations = await Promise.all(files.map(file => readFile(
      path.resolve(process.cwd(), 'server/operational/migrations', file),
      'utf8',
    )));
    cachedSql = migrations.join('\n').replace(/CREATE EXTENSION IF NOT EXISTS pgcrypto;?/i, '');
    return cachedSql;
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
      'scenario_packs', 'detection_rules',
    ];
    const result = await database.query<{ table_name: string }>(
      `SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ANY($1::text[])`,
      [requiredTables],
    );
    expect(result.rows.map(row => row.table_name).sort()).toEqual(requiredTables.sort());
  }, MIGRATION_TIMEOUT_MS);

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
  }, MIGRATION_TIMEOUT_MS);

  it('rejects cross-mode incident references at the database boundary', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());

    const eventId = '22222222-2222-4222-8222-222222222222';

    await expect(database.query(
      `INSERT INTO incidents (
         event_id, mode, title, what_changed, why_it_matters, severity, status, detected_at
       ) VALUES ($1, 'training', 'Mode mismatch', 'Test', 'Test', 'high', 'active', now())`,
      [eventId],
    )).rejects.toThrow(/mode does not match/i);
  }, MIGRATION_TIMEOUT_MS);

  it('seeds a scenario pack per operating scenario with its detection rules', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());

    const packs = await database.query<{ pack_code: string; connector_codes: string[] }>(
      'SELECT pack_code, connector_codes FROM scenario_packs WHERE active ORDER BY pack_code',
    );
    expect(packs.rows.map(row => row.pack_code)).toEqual(['cyber_incident', 'road_closure', 'sec_gameday', 'severe_weather']);

    const gameday = packs.rows.find(row => row.pack_code === 'sec_gameday');
    expect(gameday?.connector_codes).toContain('auburn-eta-spot-v1');
    expect(packs.rows.find(row => row.pack_code === 'road_closure')?.connector_codes).not.toContain('auburn-eta-spot-v1');

    const rules = await database.query<{ pack_code: string; rule_code: string; playbook: Record<string, unknown> }>(
      'SELECT pack_code, rule_code, playbook FROM detection_rules ORDER BY pack_code, rule_code',
    );
    expect(rules.rows.length).toBeGreaterThanOrEqual(12);
    for (const rule of rules.rows) {
      expect(Array.isArray(rule.playbook.commitments)).toBe(true);
      expect(Array.isArray(rule.playbook.approvals)).toBe(true);
    }

    const bound = await database.query<{ scenario_pack_code: string }>(
      `SELECT scenario_pack_code FROM operational_events WHERE event_id = '22222222-2222-4222-8222-222222222222'`,
    );
    expect(bound.rows[0].scenario_pack_code).toBe('sec_gameday');
  }, MIGRATION_TIMEOUT_MS);

  it('binds an incident to one upstream record per operating window', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());
    const eventId = '22222222-2222-4222-8222-222222222222';

    const insert = (externalKey: string) => database!.query(
      `INSERT INTO incidents (
         event_id, mode, title, what_changed, why_it_matters, severity, status, detected_at,
         scenario_pack_code, detection_rule_code, origin_connector_code, origin_external_key
       ) VALUES ($1,'live','Crash','Reported','Blocks ingress','high','active',now(),
                 'sec_gameday','algo-crash','aldot-algo-traffic-v1',$2)`,
      [eventId, externalKey],
    );

    await insert('algo-event:2357202');
    await insert('algo-event:2357999');
    await expect(insert('algo-event:2357202')).rejects.toThrow(/duplicate key|unique/i);

    const count = await database.query<{ count: string }>(
      'SELECT count(*)::text AS count FROM incidents WHERE event_id = $1',
      [eventId],
    );
    expect(Number(count.rows[0].count)).toBe(2);
  }, MIGRATION_TIMEOUT_MS);

  it('accepts steady-state, response, and recovery phases for non-event scenarios', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());
    await database.query(
      `INSERT INTO operational_events (
         mode, event_type, name, phase, status, starts_at, location_name, scenario_pack_code
       ) VALUES ('live','road_closure','Weekday mobility operations','steady_state','active',now(),'Auburn','road_closure')`,
    );
    await expect(database.query(
      `INSERT INTO operational_events (
         mode, event_type, name, phase, status, starts_at, location_name
       ) VALUES ('live','road_closure','Bad phase','halftime','active',now(),'Auburn')`,
    )).rejects.toThrow(/phase_check|violates check/i);
  }, MIGRATION_TIMEOUT_MS);

  it('versions graph state, records append-only history, and rejects cross-mode graph writes', async () => {
    database = new PGlite();
    await database.exec(await migrationSql());
    const eventId = '22222222-2222-4222-8222-222222222222';
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
  }, MIGRATION_TIMEOUT_MS);
});

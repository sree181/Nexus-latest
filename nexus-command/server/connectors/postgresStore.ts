import { randomUUID } from 'node:crypto';
import type { NormalizedObservation } from '../operational/domain.js';
import { getDatabasePool, withTransaction } from '../operational/database.js';
import type { ConnectorBatch, ConnectorDefinition, ConnectorStatusView } from './types.js';
import type { ConnectorRunCompletion, ConnectorRunStart, ConnectorStore, ObservationIngestResult } from './store.js';
import type { ConnectorTrigger } from '../operational/domain.js';

function connectionFor(definition: ConnectorDefinition, configured: boolean): string {
  if (!configured && definition.partnerApprovalRequired) return 'permission_required';
  if (!configured) return 'configuration_required';
  return definition.defaultConnectionStatus === 'permission_required' ? 'connected' : definition.defaultConnectionStatus;
}

export class PostgresConnectorStore implements ConnectorStore {
  constructor(private readonly configuredCodes: Set<string>) {}

  async register(definitions: ConnectorDefinition[]): Promise<void> {
    await withTransaction(async client => {
      for (const definition of definitions) {
        const agency = await client.query(
          `INSERT INTO agencies (code, name, agency_type, metadata)
           VALUES ($1, $2, 'data_authority', $3::jsonb)
           ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, updated_at = now()
           RETURNING agency_id`,
          [definition.ownerAgencyCode, definition.ownerAgencyName, JSON.stringify({ authority: definition.authority })],
        );
        const configured = this.configuredCodes.has(definition.code);
        await client.query(
          `INSERT INTO sources (
             owner_agency_id, code, name, source_type, schema_version, expected_cadence_seconds,
             stale_after_seconds, permitted_use, health_status, config_metadata, authority_uri,
             connector_code, data_classification, partner_approval_required, connection_status, active
           ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13,$14,$15,true)
           ON CONFLICT (code) DO UPDATE SET
             owner_agency_id = EXCLUDED.owner_agency_id,
             name = EXCLUDED.name,
             source_type = EXCLUDED.source_type,
             schema_version = EXCLUDED.schema_version,
             expected_cadence_seconds = EXCLUDED.expected_cadence_seconds,
             stale_after_seconds = EXCLUDED.stale_after_seconds,
             permitted_use = EXCLUDED.permitted_use,
             config_metadata = EXCLUDED.config_metadata,
             authority_uri = EXCLUDED.authority_uri,
             connector_code = EXCLUDED.connector_code,
             data_classification = EXCLUDED.data_classification,
             partner_approval_required = EXCLUDED.partner_approval_required,
             connection_status = EXCLUDED.connection_status,
             updated_at = now()`,
          [
            agency.rows[0].agency_id, definition.sourceCode, definition.name, definition.sourceType,
            definition.schemaVersion, definition.expectedCadenceSeconds, definition.staleAfterSeconds,
            definition.permittedUse, configured ? 'unverified' : 'disabled',
            JSON.stringify({ authority: definition.authority, requiredEnvironment: definition.requiredEnvironment }),
            definition.authorityUri, definition.code, definition.dataClassification,
            definition.partnerApprovalRequired, connectionFor(definition, configured),
          ],
        );
      }
    });
  }

  async statuses(definitions: ConnectorDefinition[]): Promise<ConnectorStatusView[]> {
    const result = await getDatabasePool().query(
      `SELECT s.source_id, s.connector_code, s.connection_status, s.health_status, s.last_attempt_at,
              s.last_success_at, s.last_event_observed_at, s.consecutive_failures,
              r.connector_run_id, r.status AS run_status, r.started_at, r.completed_at,
              r.fetched_count, r.accepted_count, r.duplicate_count, r.rejected_count, r.error_category
         FROM sources s
         LEFT JOIN LATERAL (
           SELECT * FROM connector_runs cr WHERE cr.source_id = s.source_id ORDER BY cr.started_at DESC LIMIT 1
         ) r ON true
        WHERE s.connector_code = ANY($1::text[])`,
      [definitions.map(definition => definition.code)],
    );
    const rows = new Map(result.rows.map(row => [row.connector_code, row]));
    return definitions.map(definition => {
      const row = rows.get(definition.code);
      return {
        definition,
        sourceId: row?.source_id ?? null,
        configured: this.configuredCodes.has(definition.code),
        connectionStatus: row?.connection_status ?? connectionFor(definition, this.configuredCodes.has(definition.code)),
        healthStatus: row?.health_status ?? 'unverified',
        lastAttemptAt: row?.last_attempt_at?.toISOString() ?? null,
        lastSuccessAt: row?.last_success_at?.toISOString() ?? null,
        lastEventObservedAt: row?.last_event_observed_at?.toISOString() ?? null,
        consecutiveFailures: row?.consecutive_failures ?? 0,
        latestRun: row?.connector_run_id ? {
          runId: row.connector_run_id,
          status: row.run_status,
          startedAt: row.started_at.toISOString(),
          completedAt: row.completed_at?.toISOString() ?? null,
          fetchedCount: row.fetched_count,
          acceptedCount: row.accepted_count,
          duplicateCount: row.duplicate_count,
          rejectedCount: row.rejected_count,
          errorCategory: row.error_category,
        } : null,
      } as ConnectorStatusView;
    });
  }

  async beginRun(connectorCode: string, eventId: string | null, requestId: string, trigger: ConnectorTrigger): Promise<ConnectorRunStart> {
    return withTransaction(async client => {
      const source = await client.query(
        `SELECT source_id FROM sources WHERE connector_code = $1 AND active = true FOR UPDATE`,
        [connectorCode],
      );
      if (!source.rowCount) throw new Error(`Connector source is not registered: ${connectorCode}`);
      const sourceId = source.rows[0].source_id;
      await client.query('UPDATE sources SET last_attempt_at = now(), updated_at = now() WHERE source_id = $1', [sourceId]);
      const checkpoint = await client.query('SELECT metadata, cursor_value, etag, last_modified FROM connector_checkpoints WHERE source_id = $1', [sourceId]);
      const checkpointValue = checkpoint.rowCount ? checkpoint.rows[0] : {};
      const runId = randomUUID();
      const inserted = await client.query(
        `INSERT INTO connector_runs (connector_run_id, source_id, event_id, request_id, trigger_type, status, checkpoint_before)
         VALUES ($1,$2,$3,$4,$5,'running',$6::jsonb)
         ON CONFLICT (source_id, request_id) DO NOTHING
         RETURNING connector_run_id`,
        [runId, sourceId, eventId, requestId, trigger, JSON.stringify(checkpointValue)],
      );
      if (!inserted.rowCount) {
        const existing = await client.query(
          'SELECT connector_run_id FROM connector_runs WHERE source_id = $1 AND request_id = $2',
          [sourceId, requestId],
        );
        return { runId: existing.rows[0].connector_run_id, sourceId, checkpoint: checkpointValue, claimed: false };
      }
      return { runId, sourceId, checkpoint: checkpointValue, claimed: true };
    });
  }

  async ingest(run: ConnectorRunStart, eventId: string, batch: ConnectorBatch): Promise<ObservationIngestResult> {
    return withTransaction(async client => {
      let acceptedCount = 0;
      let duplicateCount = 0;
      let rejectedCount = 0;
      // Upstream ordering is not guaranteed stable between fetches. Locking evidence rows in a
      // fixed order means two runs of the same source queue behind each other instead of
      // deadlocking on rows they each hold half of.
      const ordered = [...batch.observations].sort((left, right) => left.sourceEventId.localeCompare(right.sourceEventId));
      for (const observation of ordered) {
        try {
          const existing = await client.query(
            'SELECT evidence_id, content_hash FROM evidence_events WHERE source_id = $1 AND source_event_id = $2 FOR UPDATE',
            [run.sourceId, observation.sourceEventId],
          );
          if (existing.rowCount && existing.rows[0].content_hash === observation.contentHash) {
            // Content is unchanged, but the record is still published upstream. Recording that
            // confirmation is what lets detection tell "still in force" from "withdrawn".
            await client.query(
              'UPDATE evidence_events SET received_at = now(), connector_run_id = $2 WHERE evidence_id = $1',
              [existing.rows[0].evidence_id, run.runId],
            );
            duplicateCount += 1;
            continue;
          }
          if (existing.rowCount) {
            await client.query(
              `UPDATE evidence_events SET event_id=$1, schema_version=$2, observed_at=$3, received_at=now(), summary=$4,
                 geometry_geojson=$5::jsonb, normalized_attributes=$6::jsonb, quality_flags=$7, content_hash=$8,
                 version=version+1, connector_run_id=$9, authority_uri=$10, provenance=$11::jsonb
               WHERE evidence_id=$12`,
              [eventId, observation.provenance.schemaVersion, observation.observedAt, observation.summary,
               observation.geometryGeojson ? JSON.stringify(observation.geometryGeojson) : null,
               JSON.stringify(observation.attributes), observation.qualityFlags, observation.contentHash,
               run.runId, observation.provenance.authorityUri, JSON.stringify(observation.provenance), existing.rows[0].evidence_id],
            );
          } else {
            await client.query(
              `INSERT INTO evidence_events (
                 event_id, source_id, source_event_id, schema_version, observed_at, summary, geometry_geojson,
                 normalized_attributes, quality_flags, content_hash, connector_run_id, authority_uri, provenance
               ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,$10,$11,$12,$13::jsonb)`,
              [eventId, run.sourceId, observation.sourceEventId, observation.provenance.schemaVersion,
               observation.observedAt, observation.summary,
               observation.geometryGeojson ? JSON.stringify(observation.geometryGeojson) : null,
               JSON.stringify(observation.attributes), observation.qualityFlags, observation.contentHash,
               run.runId, observation.provenance.authorityUri, JSON.stringify(observation.provenance)],
            );
          }
          acceptedCount += 1;
        } catch (error) {
          rejectedCount += 1;
          console.error('[connector] Observation rejected', {
            connectorRunId: run.runId,
            sourceEventId: observation.sourceEventId,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }
      await client.query(
        `INSERT INTO connector_checkpoints (source_id, metadata, updated_at)
         VALUES ($1,$2::jsonb,now())
         ON CONFLICT (source_id) DO UPDATE SET metadata=EXCLUDED.metadata, updated_at=now()`,
        [run.sourceId, JSON.stringify(batch.checkpoint)],
      );
      return { acceptedCount, duplicateCount, rejectedCount };
    });
  }

  async completeRun(run: ConnectorRunStart, completion: ConnectorRunCompletion): Promise<void> {
    const observedAt = completion.upstreamObservedAt ? new Date(completion.upstreamObservedAt) : null;
    const source = await getDatabasePool().query('SELECT stale_after_seconds FROM sources WHERE source_id = $1', [run.sourceId]);
    const staleAfter = source.rows[0]?.stale_after_seconds ?? 300;
    const lagSeconds = observedAt ? Math.max(0, (Date.now() - observedAt.getTime()) / 1000) : null;
    const success = completion.status === 'succeeded' || completion.status === 'partial';
    const health = !success ? 'unavailable' : lagSeconds !== null && lagSeconds > staleAfter ? 'delayed' : 'healthy';
    await withTransaction(async client => {
      await client.query(
        `UPDATE connector_runs SET status=$1, completed_at=now(), upstream_observed_at=$2, fetched_count=$3,
           accepted_count=$4, duplicate_count=$5, rejected_count=$6, duration_ms=$7,
           error_category=$8, error_detail=$9, checkpoint_after=$10::jsonb, metadata=$11::jsonb
         WHERE connector_run_id=$12`,
        [completion.status, completion.upstreamObservedAt, completion.fetchedCount, completion.acceptedCount,
         completion.duplicateCount, completion.rejectedCount, completion.durationMs,
         completion.errorCategory ?? null, completion.errorDetail ?? null,
         JSON.stringify(completion.checkpointAfter ?? {}), JSON.stringify(completion.metadata ?? {}), run.runId],
      );
      await client.query(
        `UPDATE sources SET health_status=$1, last_success_at=CASE WHEN $2 THEN now() ELSE last_success_at END,
           last_event_observed_at=COALESCE($3,last_event_observed_at), error_category=$4,
           consecutive_failures=CASE WHEN $2 THEN 0 ELSE consecutive_failures+1 END, updated_at=now()
         WHERE source_id=$5`,
        [health, success, completion.upstreamObservedAt, completion.errorCategory ?? null, run.sourceId],
      );
    });
  }
}

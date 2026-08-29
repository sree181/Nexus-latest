/**
 * Fetches the live authoritative feeds and reports which detection rules would fire, without
 * touching the database. Use it to tune a rule before letting it open real incidents.
 *
 *   npm run detection:dry-run -- sec_gameday
 */
import { authoritativeConnectors } from '../../connectors/registry.js';
import { evaluateRules, hasPredicate, type DetectionEvidence, type DetectionRuleDefinition, type Playbook, type Severity } from './rules.js';

/** Mirrors the pack bindings seeded by the migrations. Keep both in step when a pack changes. */
const PACK_CONNECTORS: Record<string, string[]> = {
  road_closure: ['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1', 'aldot-traffic-counts-v1', 'nws-weather-alerts-v1', 'usgs-natural-hazards-v1'],
  sec_gameday: ['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1', 'auburn-eta-spot-v1', 'auburn-parking-occupancy-v1', 'auburn-emergency-access-v1', 'nws-weather-alerts-v1'],
  severe_weather: ['nws-weather-alerts-v1', 'usgs-natural-hazards-v1', 'coa-road-closures-v1', 'aldot-algo-traffic-v1', 'auburn-eta-spot-v1'],
  cyber_incident: ['nexus-siem-alerts-v1', 'coa-road-closures-v1'],
};

const PACK_RULES: Record<string, Array<{ ruleCode: string; connectorCode: string; severity: Severity }>> = {
  road_closure: [
    { ruleCode: 'algo-crash', connectorCode: 'aldot-algo-traffic-v1', severity: 'high' },
    { ruleCode: 'algo-incident', connectorCode: 'aldot-algo-traffic-v1', severity: 'medium' },
    { ruleCode: 'algo-corridor-congestion', connectorCode: 'aldot-algo-traffic-v1', severity: 'medium' },
    { ruleCode: 'city-restriction-in-effect', connectorCode: 'coa-road-closures-v1', severity: 'medium' },
    { ruleCode: 'tomtom-corridor-degraded', connectorCode: 'tomtom-traffic-flow-v1', severity: 'medium' },
    { ruleCode: 'nws-alert-active', connectorCode: 'nws-weather-alerts-v1', severity: 'high' },
    { ruleCode: 'usgs-stream-rapid-rise', connectorCode: 'usgs-natural-hazards-v1', severity: 'medium' },
  ],
  sec_gameday: [
    { ruleCode: 'algo-crash', connectorCode: 'aldot-algo-traffic-v1', severity: 'high' },
    { ruleCode: 'algo-incident', connectorCode: 'aldot-algo-traffic-v1', severity: 'medium' },
    { ruleCode: 'algo-corridor-congestion', connectorCode: 'aldot-algo-traffic-v1', severity: 'medium' },
    { ruleCode: 'city-restriction-in-effect', connectorCode: 'coa-road-closures-v1', severity: 'medium' },
    { ruleCode: 'tomtom-corridor-degraded', connectorCode: 'tomtom-traffic-flow-v1', severity: 'medium' },
    { ruleCode: 'nws-alert-active', connectorCode: 'nws-weather-alerts-v1', severity: 'critical' },
  ],
  severe_weather: [
    { ruleCode: 'nws-alert-active', connectorCode: 'nws-weather-alerts-v1', severity: 'high' },
    { ruleCode: 'usgs-stream-rapid-rise', connectorCode: 'usgs-natural-hazards-v1', severity: 'high' },
    { ruleCode: 'usgs-earthquake-felt', connectorCode: 'usgs-natural-hazards-v1', severity: 'high' },
  ],
  cyber_incident: [{ ruleCode: 'siem-critical-alert', connectorCode: 'nexus-siem-alerts-v1', severity: 'high' }],
};

const emptyPlaybook: Playbook = {
  recommendedAction: 'Dry run only.',
  expectedEffect: 'Dry run only.',
  limitations: 'Dry run only.',
  approvals: [],
  commitments: [],
};

const packCode = process.argv[2] || 'sec_gameday';
const connectorCodes = PACK_CONNECTORS[packCode];
if (!connectorCodes) {
  console.error(`Unknown scenario pack "${packCode}". Choose one of: ${Object.keys(PACK_CONNECTORS).join(', ')}`);
  process.exit(1);
}

const rules: DetectionRuleDefinition[] = PACK_RULES[packCode].map(rule => ({
  packCode,
  agentCode: 'dry-run',
  name: rule.ruleCode,
  whyItMatters: 'Dry run only.',
  affectedServices: [],
  constraints: [],
  playbook: emptyPlaybook,
  ...rule,
}));

const evidence: DetectionEvidence[] = [];
const feedReport: Array<Record<string, unknown>> = [];

for (const connector of authoritativeConnectors) {
  const code = connector.definition.code;
  if (!connectorCodes.includes(code)) continue;
  if (!connector.isConfigured()) {
    feedReport.push({ connector: code, status: 'not_configured', observations: 0 });
    continue;
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  try {
    const batch = await connector.fetch({
      eventId: '00000000-0000-4000-8000-000000000000',
      requestId: `dry-run:${code}`,
      startedAt: new Date().toISOString(),
      signal: controller.signal,
      checkpoint: {},
    });
    for (const [index, observation] of batch.observations.entries()) {
      evidence.push({
        evidenceId: `${code}#${index}`,
        connectorCode: code,
        sourceEventId: observation.sourceEventId,
        sourceName: connector.definition.name,
        summary: observation.summary,
        observedAt: observation.observedAt,
        contentHash: observation.contentHash,
        geometryGeojson: observation.geometryGeojson,
        attributes: (observation.attributes ?? {}) as Record<string, unknown>,
      });
    }
    feedReport.push({ connector: code, status: 'fetched', observations: batch.observations.length });
  } catch (error) {
    feedReport.push({ connector: code, status: 'failed', detail: error instanceof Error ? error.message : String(error) });
  } finally {
    clearTimeout(timeout);
  }
}

const matches = evaluateRules(rules, evidence);

console.log(JSON.stringify({
  packCode,
  evaluatedAt: new Date().toISOString(),
  feeds: feedReport,
  evidenceConsidered: evidence.length,
  rulesWithoutPredicate: rules.filter(rule => !hasPredicate(rule.ruleCode)).map(rule => rule.ruleCode),
  incidentsThatWouldOpen: matches.length,
  matches: matches.map(match => ({
    rule: match.rule.ruleCode,
    severity: match.severity,
    externalKey: match.externalKey,
    title: match.title,
    whatChanged: match.whatChanged,
    observedAt: match.primary.observedAt,
    citedEvidence: match.evidence.length,
  })),
}, null, 2));

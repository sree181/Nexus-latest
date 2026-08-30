BEGIN;

-- Operating windows are no longer Game Day only. Phases cover steady-state,
-- response, and recovery scenarios in addition to the event-day sequence.
ALTER TABLE operational_events DROP CONSTRAINT IF EXISTS operational_events_phase_check;
ALTER TABLE operational_events ADD CONSTRAINT operational_events_phase_check
  CHECK (phase IN (
    'readiness', 'arrival', 'ingress', 'in_game', 'egress', 'after_action', 'closed',
    'steady_state', 'response', 'recovery'
  ));

CREATE TABLE IF NOT EXISTS scenario_packs (
  pack_code text PRIMARY KEY,
  name text NOT NULL,
  event_type text NOT NULL,
  description text NOT NULL,
  default_phase text NOT NULL DEFAULT 'steady_state',
  connector_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
  agent_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- A rule is the named reason an incident may open. The predicate itself lives in
-- versioned application code; this table carries the operational metadata and the
-- playbook that a human sees and approves.
CREATE TABLE IF NOT EXISTS detection_rules (
  pack_code text NOT NULL REFERENCES scenario_packs(pack_code) ON DELETE CASCADE,
  rule_code text NOT NULL,
  connector_code text NOT NULL,
  agent_code text NOT NULL DEFAULT 'atlas',
  name text NOT NULL,
  why_it_matters text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'informational')),
  affected_services text[] NOT NULL DEFAULT ARRAY[]::text[],
  constraints text[] NOT NULL DEFAULT ARRAY[]::text[],
  playbook jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (pack_code, rule_code)
);

ALTER TABLE operational_events
  ADD COLUMN IF NOT EXISTS scenario_pack_code text REFERENCES scenario_packs(pack_code);

-- One upstream authoritative record maps to exactly one incident per event.
ALTER TABLE incidents
  ADD COLUMN IF NOT EXISTS scenario_pack_code text,
  ADD COLUMN IF NOT EXISTS detection_rule_code text,
  ADD COLUMN IF NOT EXISTS origin_connector_code text,
  ADD COLUMN IF NOT EXISTS origin_external_key text;

CREATE UNIQUE INDEX IF NOT EXISTS incidents_origin_identity_unique
  ON incidents(event_id, origin_connector_code, origin_external_key)
  WHERE origin_external_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS incidents_event_status_idx ON incidents(event_id, status);

INSERT INTO scenario_packs (pack_code, name, event_type, description, default_phase, connector_codes, agent_codes)
VALUES
  (
    'road_closure',
    'Everyday road and mobility operations',
    'road_closure',
    'Continuous weekday operating window for published restrictions, ALDOT traveler events, and corridor flow.',
    'steady_state',
    ARRAY['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1', 'aldot-traffic-counts-v1'],
    ARRAY['atlas', 'forge', 'nexus']
  ),
  (
    'sec_gameday',
    'Campus and city mobility',
    'sec_gameday',
    'Full desk roster for campus and city mobility: traffic, transit, parking, public safety, and communications.',
    'readiness',
    ARRAY['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1', 'auburn-eta-spot-v1', 'auburn-parking-occupancy-v1', 'auburn-emergency-access-v1'],
    ARRAY['atlas', 'aqua', 'sentinel', 'phoenix', 'echo', 'nexus']
  ),
  (
    'severe_weather',
    'Severe weather and natural hazard',
    'severe_weather',
    'Watches, warnings, and hazard impacts on evacuation, shelter, and emergency routes.',
    'response',
    ARRAY['nws-weather-alerts-v1', 'coa-road-closures-v1', 'aldot-algo-traffic-v1', 'auburn-eta-spot-v1'],
    ARRAY['sentinel', 'phoenix', 'atlas', 'forge', 'echo', 'nexus']
  ),
  (
    'cyber_incident',
    'Communications and cyber resilience',
    'cyber_incident',
    'Network, radio, and operational-technology disruption affecting coordination and traveler information.',
    'response',
    ARRAY['nexus-siem-alerts-v1', 'coa-road-closures-v1'],
    ARRAY['echo', 'forge', 'nexus']
  )
ON CONFLICT (pack_code) DO UPDATE SET
  name = EXCLUDED.name,
  description = EXCLUDED.description,
  default_phase = EXCLUDED.default_phase,
  connector_codes = EXCLUDED.connector_codes,
  agent_codes = EXCLUDED.agent_codes,
  updated_at = now();

INSERT INTO detection_rules (
  pack_code, rule_code, connector_code, agent_code, name, why_it_matters, severity,
  affected_services, constraints, playbook
) VALUES
  (
    'road_closure', 'algo-crash', 'aldot-algo-traffic-v1', 'atlas',
    'ALDOT-reported crash on the Auburn approach',
    'A state-reported crash blocks or narrows a corridor that local traffic and emergency access depend on.',
    'high', ARRAY['traffic', 'emergency_access'],
    ARRAY['Preserve emergency corridor', 'No traffic-signal control from Nexus', 'Manual or deep-link agency handoff only'],
    '{"recommendedAction":"Confirm the ALDOT crash record with the responsible traffic operations desk and decide whether local detour or messaging support is required.","expectedEffect":"Named agencies acknowledge the state-reported crash and coordinate local support. Nexus records the decision only.","limitations":"Derived from the public ALGO traveler record. Nexus has no dispatch, lane-level, or clearance-time data unless an agency supplies it.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Confirm the ALDOT crash location and advise whether a local detour or message is warranted.","verificationRule":"Traffic operations confirmation or cleared ALGO record","dueInMinutes":60}]}'::jsonb
  ),
  (
    'road_closure', 'algo-incident', 'aldot-algo-traffic-v1', 'atlas',
    'ALDOT-reported traffic incident',
    'A state-reported incident can reduce capacity on an approach corridor before local systems observe it.',
    'medium', ARRAY['traffic'],
    ARRAY['No traffic-signal control from Nexus', 'Manual or deep-link agency handoff only'],
    '{"recommendedAction":"Review the ALDOT incident record and decide whether local traffic operations should respond or continue monitoring.","expectedEffect":"The incident is acknowledged by a named operator and monitored until ALDOT clears it.","limitations":"Public traveler record only. No lane detail, responder status, or clearance estimate.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Monitor the corridor and report whether local intervention is required.","verificationRule":"Traffic operations report or cleared ALGO record","dueInMinutes":120}]}'::jsonb
  ),
  (
    'road_closure', 'algo-corridor-congestion', 'aldot-algo-traffic-v1', 'atlas',
    'ALDOT travel time reports corridor congestion',
    'Sustained congestion on the I-85 approach backs into local streets and delays scheduled work and transit.',
    'medium', ARRAY['traffic'],
    ARRAY['No traffic-signal control from Nexus'],
    '{"recommendedAction":"Compare the ALDOT travel-time reading with field conditions and decide whether traveler messaging or work rescheduling is warranted.","expectedEffect":"Congestion is acknowledged and, if confirmed, agencies adjust messaging or scheduled work.","limitations":"Segment-level travel time only. It does not identify the cause of the delay.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Verify corridor conditions and report whether messaging is needed.","verificationRule":"Traffic operations report or ALGO congestion returning to unaffected","dueInMinutes":90}]}'::jsonb
  ),
  (
    'road_closure', 'city-restriction-in-effect', 'coa-road-closures-v1', 'forge',
    'City restriction currently in effect',
    'A City-published block, closure, or detour that is active right now changes lane availability and routing.',
    'medium', ARRAY['traffic', 'public_works'],
    ARRAY['Preserve emergency corridor', 'Preserve ADA access', 'Manual or deep-link agency handoff only'],
    '{"recommendedAction":"Confirm the City restriction is still in place and decide whether routing, signage, or notification support is required.","expectedEffect":"The active restriction is acknowledged by a named operator and supported by the responsible agencies.","limitations":"Published City record only. Nexus does not observe field conditions or barricade placement.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Confirm the restriction with field operations and report routing impact.","verificationRule":"Field confirmation or updated City RoadClosuresPublic record","dueInMinutes":120}]}'::jsonb
  ),
  (
    'road_closure', 'tomtom-corridor-degraded', 'tomtom-traffic-flow-v1', 'atlas',
    'Licensed road flow well below free flow',
    'A monitored point running far below free-flow speed indicates a developing queue on an approach corridor.',
    'medium', ARRAY['traffic'],
    ARRAY['No traffic-signal control from Nexus'],
    '{"recommendedAction":"Check the degraded corridor point against ALDOT and City records, then decide whether to notify traffic operations.","expectedEffect":"A developing queue is reviewed before it becomes a corridor failure.","limitations":"Probe-derived speed at a single monitored point. It is not an incident report.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Assess the corridor point and report cause if identifiable.","verificationRule":"Traffic operations report or flow returning above the review threshold","dueInMinutes":60}]}'::jsonb
  ),
  (
    'sec_gameday', 'algo-crash', 'aldot-algo-traffic-v1', 'atlas',
    'ALDOT-reported crash on the Game Day approach',
    'A state-reported crash on the I-85 or US-280 approach directly delays ingress, remote-lot movement, and emergency access.',
    'high', ARRAY['traffic', 'ingress', 'emergency_access'],
    ARRAY['Preserve emergency corridor', 'Preserve ADA loading', 'No traffic-signal control from Nexus'],
    '{"recommendedAction":"Confirm the ALDOT crash with traffic operations and decide whether to hold remote-lot release and adjust ingress messaging.","expectedEffect":"Ingress agencies acknowledge the crash and coordinate holds or messaging. Nexus records the decision only.","limitations":"Public traveler record. No responder status, lane detail, or clearance estimate.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Confirm the crash location and advise on ingress routing.","verificationRule":"Traffic operations confirmation or cleared ALGO record","dueInMinutes":45},{"agencyCode":"parking-transit","requestedOutcome":"Hold or stagger remote-lot release and keep shuttle wayfinding consistent with the confirmed routing.","verificationRule":"Supervisor acknowledgement recorded in Nexus","dueInMinutes":60}]}'::jsonb
  ),
  (
    'sec_gameday', 'algo-incident', 'aldot-algo-traffic-v1', 'atlas',
    'ALDOT-reported incident on the Game Day approach',
    'A state-reported incident reduces approach capacity during the arrival and ingress window.',
    'medium', ARRAY['traffic', 'ingress'],
    ARRAY['Preserve emergency corridor', 'No traffic-signal control from Nexus'],
    '{"recommendedAction":"Review the ALDOT incident and decide whether ingress messaging or shuttle timing should change.","expectedEffect":"The incident is acknowledged and monitored through the arrival window.","limitations":"Public traveler record only.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Monitor the approach and report whether ingress routing must change.","verificationRule":"Traffic operations report or cleared ALGO record","dueInMinutes":90}]}'::jsonb
  ),
  (
    'sec_gameday', 'algo-corridor-congestion', 'aldot-algo-traffic-v1', 'atlas',
    'Approach corridor congestion during the event window',
    'Sustained I-85 congestion during arrival backs into remote lots and campus ingress.',
    'medium', ARRAY['traffic', 'ingress', 'parking'],
    ARRAY['No traffic-signal control from Nexus'],
    '{"recommendedAction":"Compare the travel-time reading with field conditions and decide whether to stagger remote-lot release or update traveler messaging.","expectedEffect":"Arrival flow is managed before the corridor saturates.","limitations":"Segment travel time only; the cause is not identified.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"parking-transit","requestedOutcome":"Adjust remote-lot release pacing and shuttle frequency within pre-approved limits.","verificationRule":"Supervisor acknowledgement recorded in Nexus","dueInMinutes":60}]}'::jsonb
  ),
  (
    'sec_gameday', 'city-restriction-in-effect', 'coa-road-closures-v1', 'forge',
    'City restriction in effect near the venue',
    'An active City restriction conflicts with published Game Day ingress, wayfinding, and emergency-lane assumptions.',
    'medium', ARRAY['traffic', 'ingress', 'event_command'],
    ARRAY['Preserve emergency corridor', 'Preserve ADA loading', 'Manual or deep-link agency handoff only'],
    '{"recommendedAction":"Confirm the City restriction with Event Command and decide whether ingress routing or wayfinding must change.","expectedEffect":"Ingress and wayfinding stay consistent with the published City record.","limitations":"Published City record only; Nexus does not observe barricades or field conditions.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Confirm the restriction and advise on ingress routing.","verificationRule":"Field confirmation or updated City record","dueInMinutes":90},{"agencyCode":"parking-transit","requestedOutcome":"Align remote-lot wayfinding and shuttle routing with the confirmed restriction.","verificationRule":"Supervisor acknowledgement recorded in Nexus","dueInMinutes":120}]}'::jsonb
  ),
  (
    'sec_gameday', 'tomtom-corridor-degraded', 'tomtom-traffic-flow-v1', 'atlas',
    'Monitored approach point well below free flow',
    'A monitored ingress point far below free flow indicates queueing that will reach the remote lots.',
    'medium', ARRAY['traffic', 'ingress'],
    ARRAY['No traffic-signal control from Nexus'],
    '{"recommendedAction":"Verify the degraded point against ALDOT and City records, then decide whether to pace remote-lot release.","expectedEffect":"Queue growth is addressed before ingress saturates.","limitations":"Probe-derived speed at one monitored point.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"parking-transit","requestedOutcome":"Pace remote-lot release within pre-approved limits and report shuttle impact.","verificationRule":"Supervisor acknowledgement recorded in Nexus","dueInMinutes":60}]}'::jsonb
  ),
  (
    'severe_weather', 'nws-alert-active', 'nws-weather-alerts-v1', 'sentinel',
    'National Weather Service alert for the operating area',
    'An active watch or warning changes shelter, evacuation, outdoor-operations, and emergency-response posture.',
    'high', ARRAY['public_safety', 'traffic', 'transit'],
    ARRAY['Preserve emergency corridor', 'Follow the jurisdiction emergency plan', 'Nexus does not issue public alerts'],
    '{"recommendedAction":"Review the National Weather Service alert and decide the operating posture with the emergency manager. Nexus drafts but never sends public messages.","expectedEffect":"Named agencies acknowledge the alert and adopt a stated posture.","limitations":"NWS product only. Nexus has no shelter status, damage assessment, or EMA resource state unless supplied.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Report route and signal impacts and stage response as directed by the emergency plan.","verificationRule":"Agency report recorded in Nexus","dueInMinutes":60}]}'::jsonb
  ),
  (
    'cyber_incident', 'siem-critical-alert', 'nexus-siem-alerts-v1', 'echo',
    'Critical security alert affecting operational systems',
    'A confirmed critical alert can degrade coordination, traveler information, or operational-technology monitoring.',
    'high', ARRAY['communications', 'operational_technology'],
    ARRAY['No network isolation from Nexus', 'No public statement without the communications owner', 'Follow the incident response plan'],
    '{"recommendedAction":"Review the security alert with the communications and IT owners and decide containment and fallback coordination. Nexus does not isolate networks.","expectedEffect":"A named owner adopts a containment and communications posture with fallback channels stated.","limitations":"Alert metadata only. Nexus holds no case detail, host data, or containment authority.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"event-mobility-command","requestedOutcome":"Confirm fallback coordination channels and record the containment decision owner.","verificationRule":"Owner acknowledgement recorded in Nexus","dueInMinutes":30}]}'::jsonb
  )
ON CONFLICT (pack_code, rule_code) DO UPDATE SET
  connector_code = EXCLUDED.connector_code,
  agent_code = EXCLUDED.agent_code,
  name = EXCLUDED.name,
  why_it_matters = EXCLUDED.why_it_matters,
  severity = EXCLUDED.severity,
  affected_services = EXCLUDED.affected_services,
  constraints = EXCLUDED.constraints,
  playbook = EXCLUDED.playbook,
  updated_at = now();

-- Opening and closing an operating window is a command-lead action.
UPDATE principal_roles
   SET scopes = array_append(scopes, 'event:manage')
 WHERE role_code = 'event_mobility_lead'
   AND NOT ('event:manage' = ANY (scopes));

UPDATE operational_events
   SET scenario_pack_code = 'sec_gameday'
 WHERE event_id = '22222222-2222-4222-8222-222222222222'
   AND scenario_pack_code IS NULL;

-- Retire the single templated advisory used before rule-based detection existed.
-- Anything a human already decided is preserved.
DELETE FROM recommendation_evidence
 WHERE recommendation_id = '44444444-4444-4444-8444-444444444444'
   AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.recommendation_id = '44444444-4444-4444-8444-444444444444');

DELETE FROM approval_requirements
 WHERE recommendation_id = '44444444-4444-4444-8444-444444444444'
   AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.recommendation_id = '44444444-4444-4444-8444-444444444444');

DELETE FROM recommendations
 WHERE recommendation_id = '44444444-4444-4444-8444-444444444444'
   AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.recommendation_id = '44444444-4444-4444-8444-444444444444');

DELETE FROM agent_findings
 WHERE incident_id = '33333333-3333-4333-8333-333333333333'
   AND NOT EXISTS (SELECT 1 FROM recommendations r WHERE r.incident_id = '33333333-3333-4333-8333-333333333333');

DELETE FROM incident_evidence
 WHERE incident_id = '33333333-3333-4333-8333-333333333333'
   AND NOT EXISTS (SELECT 1 FROM recommendations r WHERE r.incident_id = '33333333-3333-4333-8333-333333333333');

DELETE FROM incidents
 WHERE incident_id = '33333333-3333-4333-8333-333333333333'
   AND NOT EXISTS (SELECT 1 FROM recommendations r WHERE r.incident_id = '33333333-3333-4333-8333-333333333333');

COMMIT;

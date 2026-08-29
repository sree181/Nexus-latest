-- Free, no-partner hazard sources: National Weather Service products and USGS gauges/seismicity.
-- Agencies and sources are registered by the connector runtime, so this migration only binds the
-- new connectors to scenario packs and adds the rules that decide when they open an incident.

BEGIN;

UPDATE scenario_packs
   SET connector_codes = ARRAY['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1',
                               'aldot-traffic-counts-v1', 'nws-weather-alerts-v1', 'usgs-natural-hazards-v1'],
       description = 'Continuous weekday operating window for published restrictions, ALDOT traveler events, corridor flow, and weather or flooding that closes roads.',
       updated_at = now()
 WHERE pack_code = 'road_closure';

UPDATE scenario_packs
   SET connector_codes = ARRAY['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1',
                               'auburn-eta-spot-v1', 'auburn-parking-occupancy-v1', 'auburn-emergency-access-v1',
                               'nws-weather-alerts-v1'],
       updated_at = now()
 WHERE pack_code = 'sec_gameday';

UPDATE scenario_packs
   SET connector_codes = ARRAY['nws-weather-alerts-v1', 'usgs-natural-hazards-v1', 'coa-road-closures-v1',
                               'aldot-algo-traffic-v1', 'auburn-eta-spot-v1'],
       updated_at = now()
 WHERE pack_code = 'severe_weather';

INSERT INTO detection_rules (
  pack_code, rule_code, connector_code, agent_code, name, why_it_matters, severity,
  affected_services, constraints, playbook
) VALUES
  (
    'road_closure', 'nws-alert-active', 'nws-weather-alerts-v1', 'forge',
    'National Weather Service alert for Lee County',
    'A flood, wind, or winter product changes which roads are passable and which scheduled work must stop.',
    'high', ARRAY['traffic', 'public_works'],
    ARRAY['Preserve emergency corridor', 'Nexus does not issue public alerts', 'Follow the jurisdiction emergency plan'],
    '{"recommendedAction":"Read the National Weather Service product and decide whether to suspend scheduled work, pre-position barricades, or close flood-prone crossings.","expectedEffect":"Public works and traffic operations adopt a stated posture before conditions arrive.","limitations":"NWS product only. Nexus has no field observation of standing water, debris, or barricade placement.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Report which routes and scheduled work are affected and confirm the posture adopted.","verificationRule":"Agency report recorded in Nexus","dueInMinutes":90}]}'::jsonb
  ),
  (
    'sec_gameday', 'nws-alert-active', 'nws-weather-alerts-v1', 'sentinel',
    'National Weather Service alert during the event window',
    'Lightning, wind, or heat products drive stadium hold, shelter, and evacuation decisions and change every ingress and egress assumption.',
    'critical', ARRAY['public_safety', 'traffic', 'transit', 'parking'],
    ARRAY['Preserve emergency corridor', 'Follow the venue severe-weather plan', 'Nexus does not issue public alerts', 'Nexus does not order evacuation'],
    '{"recommendedAction":"Brief the National Weather Service product to the event commander and decide the venue posture under the severe-weather plan. Nexus drafts but never sends public messages.","expectedEffect":"The event commander records a posture and transit and parking adjust to it.","limitations":"NWS product only. Nexus holds no venue occupancy, shelter capacity, or lightning-detection data unless an agency supplies it.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"parking-transit","requestedOutcome":"Confirm shelter-in-place staging for lots and shuttle holds consistent with the recorded posture.","verificationRule":"Supervisor acknowledgement recorded in Nexus","dueInMinutes":30},{"agencyCode":"city-traffic","requestedOutcome":"Report route and signal impacts and stage response as directed by the venue plan.","verificationRule":"Agency report recorded in Nexus","dueInMinutes":45}]}'::jsonb
  ),
  (
    'road_closure', 'usgs-stream-rapid-rise', 'usgs-natural-hazards-v1', 'forge',
    'USGS gauge shows a rapid stream rise',
    'Lee County creeks rise ahead of the low-water crossings and underpasses that flood first.',
    'medium', ARRAY['traffic', 'public_works'],
    ARRAY['Preserve emergency corridor', 'Nexus does not close roads'],
    '{"recommendedAction":"Send public works to inspect the flood-prone crossings downstream of the gauge and decide whether a closure is warranted.","expectedEffect":"Crossings are inspected before water reaches the roadway rather than after a vehicle enters it.","limitations":"Provisional gauge readings with no published flood stage for these creeks. The reading reports water level, not road condition.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Inspect the downstream crossings and report whether any roadway is affected.","verificationRule":"Field inspection report recorded in Nexus","dueInMinutes":90}]}'::jsonb
  ),
  (
    'severe_weather', 'usgs-stream-rapid-rise', 'usgs-natural-hazards-v1', 'forge',
    'USGS gauge shows a rapid stream rise',
    'Rising creeks cut evacuation and emergency routes before any flood warning names a specific road.',
    'high', ARRAY['public_safety', 'traffic', 'public_works'],
    ARRAY['Preserve emergency corridor', 'Nexus does not close roads', 'Follow the jurisdiction emergency plan'],
    '{"recommendedAction":"Inspect the flood-prone crossings downstream of the gauge and confirm which evacuation and emergency routes remain usable.","expectedEffect":"Route viability is confirmed by a named agency before it is relied on in the response.","limitations":"Provisional gauge readings with no published flood stage for these creeks. The reading reports water level, not road condition.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Inspect downstream crossings and report route viability to the emergency manager.","verificationRule":"Field inspection report recorded in Nexus","dueInMinutes":60}]}'::jsonb
  ),
  (
    'severe_weather', 'usgs-earthquake-felt', 'usgs-natural-hazards-v1', 'forge',
    'USGS records a regional earthquake',
    'A felt earthquake requires bridge and structure inspection before routes carrying crowds are trusted.',
    'high', ARRAY['public_safety', 'traffic', 'public_works'],
    ARRAY['Preserve emergency corridor', 'Nexus does not certify structures', 'Follow the jurisdiction emergency plan'],
    '{"recommendedAction":"Request bridge and structure inspection on the routes in use and decide whether to restrict them until inspection reports back.","expectedEffect":"Structural condition is established by inspection rather than assumed.","limitations":"USGS seismic catalog only. Nexus holds no structural, utility, or damage-assessment data.","approvals":[{"agencyCode":"event-mobility-command","roleCode":"event_mobility_lead"}],"commitments":[{"agencyCode":"city-traffic","requestedOutcome":"Inspect bridges and structures on the routes in use and report condition.","verificationRule":"Inspection report recorded in Nexus","dueInMinutes":120}]}'::jsonb
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

COMMIT;

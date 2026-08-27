-- Live operational command window and named command owner.
-- This does not insert simulated incidents, recommendations, or evidence.

INSERT INTO agencies (agency_id, code, name, agency_type, metadata)
VALUES
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'event-mobility-command', 'Auburn Event Mobility Command', 'command', '{"role":"command_owner"}'::jsonb),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'parking-transit', 'Parking & Transit Operations', 'operating', '{}'::jsonb),
  ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'city-traffic', 'City of Auburn Traffic Engineering', 'operating', '{}'::jsonb)
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name,
  agency_type = EXCLUDED.agency_type,
  updated_at = now();

INSERT INTO principals (principal_id, external_subject, display_name, email, active)
VALUES (
  '11111111-1111-4111-8111-111111111111',
  'nexus-event-mobility-lead',
  'Jordan Smith',
  'jordan.smith@auburn-event-command.example',
  true
)
ON CONFLICT (principal_id) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  email = EXCLUDED.email,
  active = true,
  updated_at = now();

INSERT INTO principal_roles (principal_id, agency_id, role_code, scopes)
VALUES (
  '11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'event_mobility_lead',
  ARRAY[
    'event:read',
    'incident:read',
    'recommendation:read',
    'recommendation:approve',
    'recommendation:reject',
    'recommendation:request_revision',
    'recommendation:delegate',
    'recommendation:escalate',
    'commitment:read',
    'commitment:transition',
    'audit:read',
    'connector:read',
    'connector:run',
    'graph:read',
    'graph:ingest'
  ]
)
ON CONFLICT (principal_id, agency_id, role_code) DO UPDATE SET
  scopes = EXCLUDED.scopes;

INSERT INTO operational_events (
  event_id, mode, event_type, name, phase, status, starts_at, ends_at,
  command_owner_principal_id, command_owner_agency_id, location_name, version
)
VALUES (
  '22222222-2222-4222-8222-222222222222',
  'live',
  'sec_gameday',
  'SEC Game Day Mobility Operations',
  'readiness',
  'active',
  now() - interval '4 hours',
  now() + interval '18 hours',
  '11111111-1111-4111-8111-111111111111',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'Jordan-Hare Stadium, Auburn, Alabama',
  1
)
ON CONFLICT (event_id) DO NOTHING;

INSERT INTO event_participants (event_id, agency_id, operational_role, primary_contact_principal_id, status)
VALUES
  ('22222222-2222-4222-8222-222222222222', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'command_owner', '11111111-1111-4111-8111-111111111111', 'active'),
  ('22222222-2222-4222-8222-222222222222', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'parking_transit', NULL, 'confirmed'),
  ('22222222-2222-4222-8222-222222222222', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'traffic_operations', NULL, 'confirmed')
ON CONFLICT (event_id, agency_id, operational_role) DO NOTHING;

-- The live window is everyday Auburn mobility, not an SEC Game Day product.

UPDATE operational_events
   SET name = 'Auburn Mobility Operations',
       location_name = 'Auburn, Alabama',
       version = version + 1,
       updated_at = now()
 WHERE event_id = '22222222-2222-4222-8222-222222222222'
    OR name ILIKE '%game day%';

UPDATE scenario_packs
   SET name = 'Campus and city mobility',
       description = 'Full desk roster for campus and city mobility: traffic, transit, parking, public safety, and communications.',
       updated_at = now()
 WHERE pack_code = 'sec_gameday';

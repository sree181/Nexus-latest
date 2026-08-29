/**
 * One image serves two roles. A platform that always runs the image default would otherwise
 * start a second API and no worker, which looks healthy while ingestion silently stops between
 * deploys. An unrecognised value fails the boot rather than quietly falling back to the API.
 */
export const SERVICE_ROLES = ['api', 'connector-worker'] as const;

export type ServiceRole = typeof SERVICE_ROLES[number];

export function resolveServiceRole(value: string | undefined): ServiceRole {
  const configured = (value ?? 'api').trim();
  if (configured === '') return 'api';
  if (!(SERVICE_ROLES as readonly string[]).includes(configured)) {
    throw new Error(`NEXUS_SERVICE_ROLE must be one of ${SERVICE_ROLES.join(', ')}; received "${configured}"`);
  }
  return configured as ServiceRole;
}

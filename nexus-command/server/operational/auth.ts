import type { NextFunction, Request, Response } from 'express';
import { createRemoteJWKSet, jwtVerify } from 'jose';
import type { OperationalMode, PrincipalContext } from './domain.js';
import { OperationalError } from './errors.js';

declare global {
  namespace Express {
    interface Request {
      principal?: PrincipalContext;
      requestId?: string;
    }
  }
}

const reviewPrincipal: PrincipalContext = {
  principalId: '11111111-1111-4111-8111-111111111111',
  externalSubject: 'local-review-event-mobility-lead',
  displayName: 'Jordan Smith',
  agencyId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  agencyName: 'Auburn Event Mobility Command',
  roles: ['event_mobility_lead', 'traffic_approver'],
  scopes: [
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
    'graph:ingest',
  ],
  modes: ['live'],
};

let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;

function getReviewModePrincipal(req: Request): PrincipalContext {
  const displayName = req.header('x-review-operator-name')?.trim();
  return displayName ? { ...reviewPrincipal, displayName } : reviewPrincipal;
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter(item => typeof item === 'string') as string[];
  if (typeof value === 'string') return value.split(' ').filter(Boolean);
  return [];
}

export async function authenticateRequest(req: Request, _res: Response, next: NextFunction): Promise<void> {
  try {
    const authMode = process.env.NEXUS_AUTH_MODE || (process.env.NODE_ENV === 'production' ? 'oidc_jwt' : 'review');

    if (authMode === 'review') {
      if (process.env.NODE_ENV === 'production') {
        throw new OperationalError(500, 'INSECURE_AUTH_CONFIGURATION', 'Review authentication cannot run in production');
      }
      req.principal = getReviewModePrincipal(req);
      next();
      return;
    }

    const authorization = req.header('authorization');
    if (!authorization?.startsWith('Bearer ')) {
      throw new OperationalError(401, 'AUTHENTICATION_REQUIRED', 'A bearer token is required');
    }

    const issuer = process.env.NEXUS_OIDC_ISSUER ?? process.env.OIDC_ISSUER;
    const audience = process.env.NEXUS_OIDC_AUDIENCE ?? process.env.OIDC_AUDIENCE;
    const jwksUrl = process.env.NEXUS_OIDC_JWKS_URI ?? process.env.OIDC_JWKS_URL;
    if (!issuer || !audience || !jwksUrl) {
      throw new OperationalError(500, 'OIDC_NOT_CONFIGURED', 'OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL are required');
    }

    if (!jwks) jwks = createRemoteJWKSet(new URL(jwksUrl));
    const { payload } = await jwtVerify(authorization.slice(7), jwks, { issuer, audience });

    const principalId = payload.nexus_principal_id;
    const agencyId = payload.nexus_agency_id;
    const agencyName = payload.nexus_agency_name;
    if (typeof payload.sub !== 'string' || typeof principalId !== 'string' || typeof agencyId !== 'string' || typeof agencyName !== 'string') {
      throw new OperationalError(403, 'IDENTITY_CLAIMS_INCOMPLETE', 'Token is missing required Nexus principal and agency claims');
    }

    const modes = asStringArray(payload.nexus_modes).filter(mode => ['live', 'training', 'replay'].includes(mode)) as OperationalMode[];
    req.principal = {
      principalId,
      externalSubject: payload.sub,
      displayName: typeof payload.name === 'string' ? payload.name : payload.sub,
      agencyId,
      agencyName,
      roles: asStringArray(payload.nexus_roles),
      scopes: asStringArray(payload.nexus_scopes),
      modes,
    };
    next();
  } catch (error) {
    next(error instanceof OperationalError ? error : new OperationalError(401, 'INVALID_TOKEN', 'The bearer token is invalid'));
  }
}

export function requireScope(scope: string) {
  return (req: Request, _res: Response, next: NextFunction): void => {
    if (!req.principal?.scopes.includes(scope)) {
      next(new OperationalError(403, 'SCOPE_REQUIRED', `Required scope: ${scope}`));
      return;
    }
    next();
  };
}

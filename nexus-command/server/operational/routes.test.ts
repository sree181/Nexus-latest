import request from 'supertest';
import { beforeEach, describe, expect, it } from 'vitest';
import { createApp } from '../app.js';
import { ReviewOperationalRepository } from './reviewRepository.js';

describe('Nexus operational API', () => {
  beforeEach(() => {
    process.env.NEXUS_AUTH_MODE = 'review';
    process.env.NODE_ENV = 'test';
  });

  it('returns the active event and complete operator snapshot', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const active = await request(app).get('/api/v1/events/active?mode=live').expect(200);
    expect(active.body.data.name).toBe('Auburn Mobility Operations');

    const snapshot = await request(app).get(`/api/v1/events/${active.body.data.eventId}/snapshot`).expect(200);
    expect(snapshot.body.data.incidents).toHaveLength(1);
    expect(snapshot.body.data.decisionQueue).toHaveLength(1);
    expect(snapshot.body.data.sources).toHaveLength(4);
  });

  it('requires an idempotency key for human decisions', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    await request(app)
      .post('/api/v1/recommendations/44444444-4444-4444-8444-444444444444/decisions')
      .send({
        action: 'approve',
        recommendationVersion: 3,
        expectedState: 'awaiting_approval',
        evidenceSnapshotHash: 'review-evidence-v3-7f2a',
        reasonCode: 'EVIDENCE_REVIEWED',
      })
      .expect(422);
  });

  it('records a human approval and returns requested agency commitments', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const response = await request(app)
      .post('/api/v1/recommendations/44444444-4444-4444-8444-444444444444/decisions')
      .set('Idempotency-Key', 'integration-approval-001')
      .send({
        action: 'approve',
        recommendationVersion: 3,
        expectedState: 'awaiting_approval',
        evidenceSnapshotHash: 'review-evidence-v3-7f2a',
        reasonCode: 'EVIDENCE_AND_CONSTRAINTS_REVIEWED',
      })
      .expect(200);

    expect(response.body.data.recommendation.state).toBe('approved');
    expect(response.body.data.createdCommitments).toHaveLength(2);
    expect(response.body.data.createdCommitments[0].state).toBe('requested');
  });

  it('returns a safe health response without exposing operational data', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const response = await request(app).get('/api/health').expect(200);
    expect(response.body).toEqual(expect.objectContaining({ status: 'degraded', database: 'review_repository' }));
    expect(response.body).not.toHaveProperty('sources');
  });

  it('returns the ATLAS profile a command lead can configure', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const response = await request(app).get('/api/v1/desks/atlas/profile').expect(200);
    expect(response.body.data.deskCode).toBe('atlas');
    expect(response.body.data.role).toBeTruthy();
    expect(response.body.data.tools.some((tool: { name: string }) => tool.name === 'search_policies')).toBe(true);
    expect(response.body.data.runtime).not.toHaveProperty('apiKey');
  });

  it('returns the AQUA profile a command lead can configure', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const response = await request(app).get('/api/v1/desks/aqua/profile').expect(200);
    expect(response.body.data.deskCode).toBe('aqua');
    expect(response.body.data.tools.some((tool: { name: string }) => tool.name === 'list_aqua_evidence')).toBe(true);
    expect(response.body.data.locked.actionFamilies).toContain('confirm_lot_shuttle');
    expect(response.body.data.runtime).not.toHaveProperty('apiKey');
  });

  it('does not expose a profile for a rule-only desk', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    await request(app).get('/api/v1/desks/echo/profile').expect(404);
  });

  it('exposes public identity configuration without a bearer token', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const response = await request(app).get('/api/v1/auth/config').expect(200);
    expect(response.body.data.loginRequired).toBe(false);
    expect(response.body.data.wallPublic).toBe(true);
    expect(response.body.data).toEqual(expect.objectContaining({
      configured: false,
      scopes: 'openid profile email',
    }));
  });

  it('lets the command wall read the snapshot without a bearer token, and still refuses unsigned decisions', async () => {
    process.env.NEXUS_AUTH_MODE = 'oidc_jwt';
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const active = await request(app).get('/api/v1/events/active?mode=live').expect(200);
    await request(app).get(`/api/v1/events/${active.body.data.eventId}/snapshot`).expect(200);
    await request(app)
      .post('/api/v1/recommendations/44444444-4444-4444-8444-444444444444/decisions')
      .set('Idempotency-Key', 'wall-cannot-sign-001')
      .send({
        action: 'approve',
        recommendationVersion: 3,
        expectedState: 'awaiting_approval',
        evidenceSnapshotHash: 'review-evidence-v3-7f2a',
        reasonCode: 'EVIDENCE_AND_CONSTRAINTS_REVIEWED',
      })
      .expect(401);
  });
});

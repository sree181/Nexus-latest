import type {
  Commitment,
  CommitmentTransitionCommand,
  DecisionCommand,
  DecisionResult,
  OpenOperatingWindowCommand,
  OperationalEvent,
  OperationalMode,
  PrincipalContext,
  Recommendation,
  ScenarioPack,
  SystemStatus,
} from './domain.js';
import { hasDatabaseConfiguration } from './database.js';
import { OperationalError } from './errors.js';
import { PostgresOperationalRepository } from './postgresRepository.js';
import type { AuditRecord, EventStreamRecord, OperationalRepository, OperationalSnapshot } from './repository.js';
import { ReviewOperationalRepository } from './reviewRepository.js';

class ConfigurationRequiredRepository implements OperationalRepository {
  async systemStatus(): Promise<SystemStatus> {
    return {
      status: 'configuration_required',
      mode: null,
      checkedAt: new Date().toISOString(),
      database: 'not_configured',
      sourceSummary: { healthy: 0, delayed: 0, unavailable: 0, unverified: 0 },
      message: 'DATABASE_URL and agency identity configuration are required before live operations can open.',
    };
  }

  private unavailable(): never {
    throw new OperationalError(503, 'OPERATIONAL_STORAGE_NOT_CONFIGURED', 'Persistent operational storage is not configured');
  }

  async scenarioPacks(): Promise<ScenarioPack[]> { return this.unavailable(); }
  async activeEvent(_mode: OperationalMode): Promise<OperationalEvent | null> { return this.unavailable(); }
  async openOperatingWindow(
    _command: OpenOperatingWindowCommand,
    _principal: PrincipalContext,
    _idempotencyKey: string,
    _requestId: string,
  ): Promise<OperationalEvent> { return this.unavailable(); }
  async closeOperatingWindow(
    _eventId: string,
    _principal: PrincipalContext,
    _requestId: string,
  ): Promise<OperationalEvent> { return this.unavailable(); }
  async snapshot(_eventId: string, _principal: PrincipalContext): Promise<OperationalSnapshot> { return this.unavailable(); }
  async recommendation(_recommendationId: string, _principal: PrincipalContext): Promise<Recommendation> { return this.unavailable(); }
  async decide(
    _recommendationId: string,
    _command: DecisionCommand,
    _principal: PrincipalContext,
    _idempotencyKey: string,
    _requestId: string,
  ): Promise<DecisionResult> { return this.unavailable(); }
  async transitionCommitment(
    _commitmentId: string,
    _command: CommitmentTransitionCommand,
    _principal: PrincipalContext,
    _idempotencyKey: string,
    _requestId: string,
  ): Promise<Commitment> { return this.unavailable(); }
  async audit(_eventId: string, _after: number, _limit: number, _principal: PrincipalContext): Promise<AuditRecord[]> { return this.unavailable(); }
  async stream(_eventId: string, _after: number, _principal: PrincipalContext): Promise<EventStreamRecord[]> { return this.unavailable(); }
}

export function createOperationalRepository(): OperationalRepository {
  const requested = process.env.NEXUS_REPOSITORY;

  if (requested === 'review') {
    if (process.env.NODE_ENV === 'production') {
      throw new Error('NEXUS_REPOSITORY=review is forbidden in production');
    }
    return new ReviewOperationalRepository();
  }

  if (hasDatabaseConfiguration()) return new PostgresOperationalRepository();
  if (process.env.NODE_ENV !== 'production') return new ReviewOperationalRepository();
  return new ConfigurationRequiredRepository();
}

export class OperationalError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'OperationalError';
  }
}

export function notFound(resource: string, id: string): OperationalError {
  return new OperationalError(404, 'NOT_FOUND', `${resource} ${id} was not found`);
}

export function conflict(code: string, message: string, details?: Record<string, unknown>): OperationalError {
  return new OperationalError(409, code, message, details);
}

export function forbidden(message = 'The authenticated principal is not authorized for this action'): OperationalError {
  return new OperationalError(403, 'FORBIDDEN', message);
}

export function validation(message: string, details?: Record<string, unknown>): OperationalError {
  return new OperationalError(422, 'VALIDATION_FAILED', message, details);
}

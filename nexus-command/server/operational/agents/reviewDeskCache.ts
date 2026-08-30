let invalidate: (() => void) | null = null;

export function setReviewDeskInvalidator(fn: () => void): void {
  invalidate = fn;
}

export function invalidateReviewDeskFindings(): void {
  invalidate?.();
}

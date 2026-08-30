import { DeskConfigDialog } from './DeskConfigDialog';

export function AtlasConfigDialog({
  busy, error, onClose, onSaved,
}: {
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  return (
    <DeskConfigDialog
      deskCode="atlas"
      busy={busy}
      error={error}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

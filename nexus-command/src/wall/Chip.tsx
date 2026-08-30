export function Chip({
  tone,
  label,
}: {
  tone: 'danger' | 'accent' | 'ok' | 'muted' | 'info';
  label: string;
}) {
  return <span className={`wall-chip wall-chip--${tone}`}>{label}</span>;
}

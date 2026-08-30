import { useRef } from 'react';

export function WallButton({
  tone,
  label,
  onPress,
}: {
  tone: 'primary' | 'secondary' | 'text' | 'clear';
  label: string;
  onPress: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <button
      ref={ref}
      type="button"
      className={`wall-button wall-button--${tone}`}
      onPointerDown={event => {
        event.preventDefault();
        ref.current?.classList.add('is-pressed');
        window.setTimeout(() => ref.current?.classList.remove('is-pressed'), 150);
        onPress();
      }}
    >
      {label}
    </button>
  );
}

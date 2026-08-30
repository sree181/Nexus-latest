import { useEffect, useRef } from 'react';

export function useIdleTimer(ms: number, onIdle: () => void, active = true) {
  const onIdleRef = useRef(onIdle);
  onIdleRef.current = onIdle;

  useEffect(() => {
    if (!active) return undefined;
    let timer = window.setTimeout(() => onIdleRef.current(), ms);
    const bump = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => onIdleRef.current(), ms);
    };
    window.addEventListener('pointerdown', bump, true);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('pointerdown', bump, true);
    };
  }, [active, ms]);
}

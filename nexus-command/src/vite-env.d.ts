/// <reference types="vite/client" />

import type { ComponentType } from 'react';
import type L from 'leaflet';

declare global {
  interface Window {
    L: typeof L;
  }
}

declare module '*.jsx' {
  const Component: ComponentType<Record<string, unknown>>;
  export default Component;
}

export {};

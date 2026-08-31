import React from 'react';
import ReactDOM from 'react-dom/client';
import { AuthGate } from '../auth/AuthGate';
import '../auth/auth-gate.css';
import { NexusWall } from '../nexus';

const params = new URLSearchParams(window.location.search);
const mode = params.get('mode') === 'walk-up' ? 'walk-up' : 'wall';
const screen = params.get('screen') ?? 'operations';
const witness = params.get('witness') === 'signed' ? 'signed' : 'awaiting signature';
const reach = params.get('reach') === '1';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate>
      <NexusWall
        displayMode={mode}
        screen={screen}
        witnessState={witness}
        reachOverlay={reach}
      />
    </AuthGate>
  </React.StrictMode>,
);

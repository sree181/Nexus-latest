import { useState } from 'react';
import { OperationalCommandCenter } from './components/OperationalCommandCenter';
import { OperationalGraphWorkspace } from './components/OperationalGraphWorkspace';
import './graphWorkspace.css';

export default function App() {
  const [workspace, setWorkspace] = useState<'command' | 'graph'>('command');
  return <div className="app-workspace">
    <nav className="workspace-switcher" aria-label="Nexus workspace">
      <strong>Nexus operational workspace</strong>
      <div><button className={workspace === 'command' ? 'active' : ''} onClick={() => setWorkspace('command')}>Command center</button><button className={workspace === 'graph' ? 'active' : ''} onClick={() => setWorkspace('graph')}>Operational graph</button></div>
    </nav>
    {workspace === 'command' ? <OperationalCommandCenter /> : <OperationalGraphWorkspace />}
  </div>;
}

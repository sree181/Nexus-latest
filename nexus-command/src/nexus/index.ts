export { default as NexusWall } from './wall/NexusWall.jsx';
export { default as NexusDesk } from './desk/NexusDesk.jsx';

export type NexusWallProps = {
  displayMode?: 'wall' | 'walk-up';
  screen?: 'operations' | 'deliberation' | 'evidence' | 'decision' | 'commitments';
  witnessState?: 'awaiting signature' | 'signed';
  reachOverlay?: boolean;
};


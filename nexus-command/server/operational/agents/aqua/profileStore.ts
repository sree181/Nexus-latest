import { createDeskProfileStore } from '../profileStoreFactory.js';
import type { DeskAgentProfile } from '../profileTypes.js';
import { AQUA_TOOL_CATALOG, defaultAquaProfile } from './catalog.js';

const store = createDeskProfileStore('aqua', AQUA_TOOL_CATALOG, defaultAquaProfile);

export const aquaProfileWriteSchema = store.writeSchema;
export type AquaProfileWrite = Parameters<typeof store.save>[0];

export function loadAquaProfile(): DeskAgentProfile {
  return store.load();
}

export function saveAquaProfile(input: unknown): DeskAgentProfile {
  return store.save(input);
}

export function resetAquaProfile(): DeskAgentProfile {
  return store.reset();
}

export function replaceAquaProfileForTest(profile: DeskAgentProfile | null): void {
  store.replaceForTest(profile);
}

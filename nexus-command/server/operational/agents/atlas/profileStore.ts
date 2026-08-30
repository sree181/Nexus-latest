import { createDeskProfileStore } from '../profileStoreFactory.js';
import type { DeskAgentProfile } from '../profileTypes.js';
import { ATLAS_TOOL_CATALOG, defaultAtlasProfile } from './catalog.js';

const store = createDeskProfileStore('atlas', ATLAS_TOOL_CATALOG, defaultAtlasProfile);

export const atlasProfileWriteSchema = store.writeSchema;
export type AtlasProfileWrite = Parameters<typeof store.save>[0];

export function loadAtlasProfile(): DeskAgentProfile {
  return store.load();
}

export function saveAtlasProfile(input: unknown): DeskAgentProfile {
  return store.save(input);
}

export function resetAtlasProfile(): DeskAgentProfile {
  return store.reset();
}

export function replaceAtlasProfileForTest(profile: DeskAgentProfile | null): void {
  store.replaceForTest(profile);
}

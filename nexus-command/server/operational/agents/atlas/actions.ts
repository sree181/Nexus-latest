export const ATLAS_ACTION_FAMILIES = ['confirm_corridor', 'hold_no_change', 'note_events_only'] as const;

export type AtlasActionFamily = (typeof ATLAS_ACTION_FAMILIES)[number];

export const ATLAS_ACTION_TEXT: Record<AtlasActionFamily, string> = {
  confirm_corridor: 'Confirm the corridor picture with traffic operations before committing to a routing or messaging change.',
  hold_no_change: 'Hold any routing or messaging change until traffic operations confirms there is headroom on the approach.',
  note_events_only: 'State-reported events are present; confirm with traffic operations before treating the corridor as degraded.',
};

export function isAtlasActionFamily(value: string): value is AtlasActionFamily {
  return (ATLAS_ACTION_FAMILIES as readonly string[]).includes(value);
}

export function actionTextFor(family: string): string | null {
  return isAtlasActionFamily(family) ? ATLAS_ACTION_TEXT[family] : null;
}

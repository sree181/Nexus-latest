export const AQUA_ACTION_FAMILIES = ['confirm_lot_shuttle', 'hold_for_occupancy', 'note_shuttles_only'] as const;
export type AquaActionFamily = (typeof AQUA_ACTION_FAMILIES)[number];

export const AQUA_ACTION_TEXT: Record<AquaActionFamily, string> = {
  confirm_lot_shuttle: 'Ask Parking and Transit to confirm lot and shuttle state before any remote-lot or staging change.',
  hold_for_occupancy: 'Hold any remote-lot or staging change until lot occupancy can be confirmed by Parking.',
  note_shuttles_only: 'Shuttle movement is visible; confirm lot state with Parking before treating occupancy as known.',
};

export function isAquaActionFamily(value: string): value is AquaActionFamily {
  return (AQUA_ACTION_FAMILIES as readonly string[]).includes(value);
}

export function aquaActionTextFor(family: string): string | null {
  return isAquaActionFamily(family) ? AQUA_ACTION_TEXT[family] : null;
}

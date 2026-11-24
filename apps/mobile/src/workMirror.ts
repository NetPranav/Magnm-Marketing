// What Library, Plan and Create read from this account's cloud mirror, one small query per kind so none crowds the
// others out: the laptop's assets, its schedules, the editorial calendar, the briefs (with the weekly plan's slots),
// the task board and the automatic-work state.

export const WORK_MIRROR_QUERIES: ReadonlyArray<{ kind: string; filter: string; limit: number }> = [
  { kind: 'asset', filter: 'kind=eq.asset', limit: 100 },
  { kind: 'schedule', filter: 'kind=eq.schedule', limit: 20 },
  { kind: 'editorial_plan', filter: 'kind=eq.editorial_plan', limit: 1 },
  { kind: 'brief', filter: 'kind=eq.brief', limit: 40 },
  // The task board (one record per item) and the automatic-work state, both made by the laptop.
  { kind: 'board_item', filter: 'kind=eq.board_item', limit: 100 },
  { kind: 'automation', filter: 'kind=eq.automation', limit: 1 },
];

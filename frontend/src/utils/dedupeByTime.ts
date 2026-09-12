/**
 * lightweight-charts requires strictly-ascending, unique-per-bar timestamps.
 * At 1Hz polling, two snapshots can legitimately land in the same integer
 * second (scheduler jitter, coalesced ticks) -- collapse those to the last
 * value for that second rather than letting setData() throw.
 */
export function dedupeByTime<T extends { time: number }>(rows: T[]): T[] {
  const byTime = new Map<number, T>()
  for (const row of rows) byTime.set(row.time, row)
  return Array.from(byTime.values()).sort((a, b) => a.time - b.time)
}

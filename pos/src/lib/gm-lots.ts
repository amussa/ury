// Server-owned batch selections are informational; the browser never chooses them.
export function lotLabel(snapshot?: string | null): string {
  if (!snapshot) return '';
  try {
    const rows = JSON.parse(snapshot) as { batch?: string; quantity?: number; expired?: boolean }[];
    if (!Array.isArray(rows)) return '';
    return rows.map(row => String(row.batch || '') + ' · ' + Number(row.quantity || 0) + (row.expired ? ' · validade ultrapassada' : '')).join('; ');
  } catch { return ''; }
}

export function formatMoney(value: number, currency: string, symbol: string | null): string {
  if (symbol) {
    return `${new Intl.NumberFormat('pt-MZ', { maximumFractionDigits: 2 }).format(value)} ${symbol}`;
  }

  try {
    return new Intl.NumberFormat('pt-MZ', {
      style: 'currency',
      currency: currency || 'MZN',
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${new Intl.NumberFormat('pt-MZ', { maximumFractionDigits: 2 }).format(value)} ${currency}`;
  }
}

export function createRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }

  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function resolveAssetUrl(path: string | null): string | null {
  if (!path) return null;
  if (/^(https?:|data:|blob:)/i.test(path)) return path;
  return path.startsWith('/') ? path : `/${path}`;
}

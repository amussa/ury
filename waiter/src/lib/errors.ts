interface FrappeErrorLike {
  _server_messages?: string;
  message?: string;
  exc?: string;
  httpStatus?: number;
  status?: number;
  statusCode?: number;
  response?: {
    status?: number;
    data?: FrappeErrorLike;
  };
}

function getHttpStatus(error: FrappeErrorLike): number | undefined {
  return error.httpStatus
    ?? error.status
    ?? error.statusCode
    ?? error.response?.status
    ?? error.response?.data?.httpStatus
    ?? error.response?.data?.status
    ?? error.response?.data?.statusCode;
}

function parseServerMessages(serialized: string): string | null {
  try {
    const outer = JSON.parse(serialized) as unknown;
    if (!Array.isArray(outer)) return null;

    for (const entry of outer) {
      if (typeof entry !== 'string') continue;
      try {
        const decoded = JSON.parse(entry) as { message?: unknown };
        if (typeof decoded.message === 'string' && decoded.message.trim()) {
          return decoded.message;
        }
      } catch {
        if (entry.trim()) return entry;
      }
    }
  } catch {
    return null;
  }

  return null;
}

export function getErrorMessage(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object') return fallback;
  const frappeError = error as FrappeErrorLike;
  const responseError = frappeError.response?.data;

  const serverMessages = frappeError._server_messages ?? responseError?._server_messages;
  if (serverMessages) {
    const serverMessage = parseServerMessages(serverMessages);
    if (serverMessage) return serverMessage;
  }

  const message = responseError?.message ?? frappeError.message;
  if (typeof message === 'string' && message.trim()) {
    return message;
  }

  return fallback;
}

export function isAuthenticationError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const frappeError = error as FrappeErrorLike;
  const status = getHttpStatus(frappeError);
  if (status === 401) return true;

  const text = `${frappeError.message ?? ''} ${frappeError.exc ?? ''} ${frappeError.response?.data?.message ?? ''} ${frappeError.response?.data?.exc ?? ''}`.toLowerCase();
  return text.includes('login required') || text.includes('session expired') || text.includes('logged out');
}

export function isAuthorizationError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  return getHttpStatus(error as FrappeErrorLike) === 403;
}

export function isUnknownSubmissionOutcome(error: unknown): boolean {
  if (!error || typeof error !== 'object') return true;
  const frappeError = error as FrappeErrorLike;
  const status = getHttpStatus(frappeError);

  if (status === 409) {
    const message = getErrorMessage(error, '').toLocaleLowerCase('pt');
    const requestStillRunning = [
      'still being processed',
      'retry shortly',
      'request in progress',
      'ainda está a ser processado',
      'ainda esta a ser processado',
      'pedido em processamento',
      'tente novamente em breve',
    ].some((fragment) => message.includes(fragment));
    if (requestStillRunning) return true;
  }

  // No response or a gateway/server failure can happen after the server has
  // accepted the request. Retrying with the same request_id is the safe path.
  return status === undefined || status === 0 || status === 408 || status === 425 || status === 429 || status >= 500;
}

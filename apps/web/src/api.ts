/** Typed HTTP error returned by the local Magnm engine API. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type UnauthorizedListener = () => void;

const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Register a callback invoked whenever any API request returns HTTP 401. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

async function parseErrorMessage(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { error?: unknown };
    if (typeof data?.error === 'string' && data.error.trim()) {
      return data.error;
    }
  } catch {
    // Ignore non-JSON error bodies and fall back to status text.
  }
  return response.statusText || `Request failed (${response.status})`;
}

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
  };
  let serializedBody: string | undefined;
  if (method === 'POST') {
    headers['Content-Type'] = 'application/json';
    serializedBody = JSON.stringify(body === undefined ? {} : body);
  }

  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers,
    body: serializedBody,
  });

  if (response.status === 401) {
    const message = await parseErrorMessage(response);
    for (const listener of unauthorizedListeners) {
      listener();
    }
    throw new ApiError(401, message);
  }

  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new ApiError(response.status, message);
  }

  return (await response.json()) as T;
}

export const api = {
  get<T>(path: string): Promise<T> {
    return request<T>('GET', path);
  },
  post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>('POST', path, body);
  },
};

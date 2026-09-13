import { vi } from 'vitest';

import type { WireEvent } from '../src/index.js';

export interface Captured {
  url: string;
  init: RequestInit;
  events: WireEvent[];
  headers: Record<string, string>;
}

/** A fetch mock that records requests and answers with the given statuses in order (last repeats). */
export function mockFetch(...responses: Array<number | Error>) {
  const calls: Captured[] = [];
  let i = 0;
  const fn = vi.fn((input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const body = JSON.parse(init.body as string) as { events: WireEvent[] };
    calls.push({
      url: input instanceof Request ? input.url : input.toString(),
      init,
      events: body.events,
      headers: (init.headers ?? {}) as Record<string, string>,
    });
    const r = responses.length === 0 ? 204 : responses[Math.min(i, responses.length - 1)]!;
    i += 1;
    if (r instanceof Error) return Promise.reject(r);
    return Promise.resolve(new Response(null, { status: r }));
  });
  return { fetch: fn as unknown as typeof fetch, calls };
}

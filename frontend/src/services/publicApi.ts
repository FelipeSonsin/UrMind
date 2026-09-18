import { z } from 'zod';
import {
  publicEventDetailSchema,
  publicEventSchema,
  publicScoutSchema,
  publicStatusSchema,
  transparencySchema,
} from '../domain/public';

// Painel público: sem token e somente leitura. Mesmo cliente HTTP do restante do
// app (fetch + zod + AbortSignal); nenhum cliente paralelo.
const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '') + '/public';

async function get<T>(path: string, schema: z.ZodType<T>, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(8000);
  try {
    const response = await fetch(`${base}${path}`, {
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      const detail = await response
        .json()
        .then((body: { detail?: unknown }) => (typeof body.detail === 'string' ? body.detail : ''))
        .catch(() => '');
      throw new Error(detail || `A API respondeu com erro ${response.status}.`);
    }
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success) throw new Error('A resposta da API não corresponde ao contrato público.');
    return parsed.data;
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof TypeError) throw new Error('Não foi possível conectar ao UrMind.');
    if (error instanceof DOMException && error.name === 'TimeoutError')
      throw new Error('O UrMind demorou a responder.');
    throw error;
  }
}

export interface EventQuery {
  limit?: number;
  urmind_class?: string;
  status?: string;
  bbox?: { south: number; west: number; north: number; east: number };
}

export const publicApi = {
  status: (signal?: AbortSignal) => get('/status', publicStatusSchema, signal),
  scout: (signal?: AbortSignal) => get('/scout', publicScoutSchema, signal),
  transparency: (signal?: AbortSignal) => get('/transparency', transparencySchema, signal),
  event: (id: string, signal?: AbortSignal) =>
    get(`/events/${id}`, publicEventDetailSchema, signal),
  events: (query: EventQuery = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    params.set('limit', String(query.limit ?? 50));
    if (query.urmind_class) params.set('urmind_class', query.urmind_class);
    if (query.status) params.set('status', query.status);
    if (query.bbox)
      for (const [key, value] of Object.entries(query.bbox)) params.set(key, String(value));
    return get(`/events?${params}`, z.array(publicEventSchema), signal);
  },
};

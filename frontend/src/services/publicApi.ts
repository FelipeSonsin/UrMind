import { z } from 'zod';
import {
  issueTaxonomySchema,
  publicEventDetailSchema,
  publicEventSchema,
  publicScoutSchema,
  publicStatusSchema,
  transparencySchema,
} from '../domain/public';

import { requestJson } from './api';
import { auth } from './auth';
import { captureMarkerSchema } from '../domain/contracts';

// Painel público: sem token e somente leitura, sobre o mesmo cliente HTTP da área logada.
const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '') + '/public';

function get<T>(
  path: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
  token?: string | null,
): Promise<T> {
  return requestJson(`${base}${path}`, schema, {
    signal,
    token,
    cache: token ? 'no-store' : undefined,
    messages: {
      network: 'Não foi possível conectar ao UrMind.',
      timeout: 'O UrMind demorou a responder.',
      contract: 'A resposta da API não corresponde ao contrato público.',
    },
  });
}

export interface EventQuery {
  limit?: number;
  urmind_class?: string;
  status?: string;
  bbox?: { south: number; west: number; north: number; east: number };
}

export const publicApi = {
  captureMarkers: (signal?: AbortSignal) =>
    get('/capture-markers', z.array(captureMarkerSchema), signal),
  status: (signal?: AbortSignal) => get('/status', publicStatusSchema, signal),
  scout: (signal?: AbortSignal) => get('/scout', publicScoutSchema, signal),
  transparency: (signal?: AbortSignal) => get('/transparency', transparencySchema, signal),
  taxonomy: (signal?: AbortSignal) => get('/taxonomy', issueTaxonomySchema, signal),
  // Existing owner session only: browsing never creates an anonymous account.
  event: async (id: string, signal?: AbortSignal) =>
    get(`/events/${id}`, publicEventDetailSchema, signal, await auth.accessToken()),
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

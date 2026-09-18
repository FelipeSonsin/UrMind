import { z } from 'zod';
import {
  eventDetailSchema,
  eventSchema,
  uploadResultSchema,
  type ReviewPayload,
} from '../domain/contracts';
import { auth } from './auth';
import type { CaptureDraft } from './drafts';

const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');

export class UnauthorizedError extends Error {
  constructor() {
    super('Sessão expirada ou ausente. Entre novamente.');
  }
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = 8000, signal, ...rest } = init;
  const timeout = AbortSignal.timeout(timeoutMs);
  const token = await auth.accessToken();
  try {
    const response = await fetch(`${base}${path}`, {
      ...rest,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      headers: {
        Accept: 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...rest.headers,
      },
    });
    if (response.status === 401) throw new UnauthorizedError();
    if (!response.ok) {
      const detail = await response
        .json()
        .then((body: { detail?: unknown }) => (typeof body.detail === 'string' ? body.detail : ''))
        .catch(() => '');
      throw new Error(
        response.status === 503
          ? 'Banco ou integração indisponível ou ainda não configurado.'
          : detail || `A API respondeu com erro ${response.status}.`,
      );
    }
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success) throw new Error('A resposta da API não corresponde ao contrato esperado.');
    return parsed.data;
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof TypeError) throw new Error('Não foi possível conectar ao backend.');
    if (error instanceof DOMException && error.name === 'TimeoutError')
      throw new Error('O backend demorou a responder. Tente novamente.');
    throw error;
  }
}

export const api = {
  health: (signal?: AbortSignal) =>
    request('/health', z.object({ status: z.string(), database: z.string().optional() }), {
      signal,
    }),
  events: (signal?: AbortSignal) => request('/events?limit=500', z.array(eventSchema), { signal }),
  me: (signal?: AbortSignal) =>
    request(
      '/me',
      z.object({ id: z.string(), email: z.string().nullable(), can_review: z.boolean() }),
      { signal },
    ),
  event: (id: string, signal?: AbortSignal) =>
    request(`/events/${id}`, eventDetailSchema, { signal }),
  review: (id: string, payload: ReviewPayload) =>
    request(`/events/${id}/reviews`, z.object({ status: z.string() }), {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { 'Content-Type': 'application/json' },
    }),
  uploadPhoto(draft: CaptureDraft) {
    const form = new FormData();
    form.append('file', draft.photo, draft.filename || 'foto.jpg');
    form.append('source', draft.source);
    if (draft.coordinate) {
      form.append('latitude', String(draft.coordinate.latitude));
      form.append('longitude', String(draft.coordinate.longitude));
      if (draft.coordinate.accuracy_m != null)
        form.append('accuracy_m', String(draft.coordinate.accuracy_m));
      form.append('location_source', draft.source_location === 'manual' ? 'manual' : 'gps_device');
    }
    form.append('tz_offset_minutes', String(-new Date().getTimezoneOffset()));
    return request('/captures/photo', uploadResultSchema, {
      method: 'POST',
      body: form,
      timeoutMs: 60000,
    });
  },
};

import { z } from 'zod';
import { eventSchema } from '../domain/contracts';

const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
async function get<T>(path: string, schema: z.ZodType<T>, signal?: AbortSignal): Promise<T> {
  try {
    const timeout = AbortSignal.timeout(8000);
    const response = await fetch(`${base}${path}`, {
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      headers: { Accept: 'application/json' },
    });
    if (!response.ok)
      throw new Error(
        response.status === 503
          ? 'Banco indisponível ou ainda não configurado.'
          : `A API respondeu com erro ${response.status}.`,
      );
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success) throw new Error('A resposta da API não corresponde ao contrato esperado.');
    return parsed.data;
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof TypeError) throw new Error('Não foi possível conectar ao backend local.');
    if (error instanceof DOMException && error.name === 'TimeoutError')
      throw new Error('O backend demorou a responder. Tente novamente.');
    throw error;
  }
}
export const api = {
  health: (signal?: AbortSignal) =>
    get('/health', z.object({ status: z.string(), database: z.string().optional() }), signal),
  events: (signal?: AbortSignal) => get('/events?limit=500', z.array(eventSchema), signal),
};

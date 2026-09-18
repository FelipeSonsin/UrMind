import { afterEach, describe, expect, it, vi } from 'vitest';
import { publicApi } from './publicApi';

const status = {
  api: { name: 'api', status: 'ok', detail: null },
  database: { name: 'database', status: 'ok', detail: null },
  detector: { name: 'detector', status: 'degraded', detail: 'modelo em estágio inicial' },
  scout: { name: 'scout', status: 'unavailable', detail: 'nenhum dispositivo registrado' },
  last_event_at: null,
  events_total: 0,
  road_segments_total: 832,
  pilot_area: 'Liberdade / FECAP',
  checked_at: '2026-09-17T12:00:00+00:00',
};

function respond(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const fetchMock = vi.fn(async (_url: string, _options?: RequestInit) => ({
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe('cliente do painel público', () => {
  it('lê o estado do sistema sem enviar credencial', async () => {
    const fetchMock = respond(status);
    const parsed = await publicApi.status();
    expect(parsed.road_segments_total).toBe(832);
    expect(parsed.scout.status).toBe('unavailable');
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/public/status');
    expect(JSON.stringify(options?.headers)).not.toMatch(/authorization|apikey/i);
  });

  it('rejeita resposta que não corresponde ao contrato público', async () => {
    respond({ ...status, events_total: 'muitos' });
    await expect(publicApi.status()).rejects.toThrow(/contrato público/);
  });

  it('propaga o motivo devolvido pela API em vez de inventar um', async () => {
    respond({ detail: 'ocorrência não encontrada' }, { ok: false, status: 404 });
    await expect(publicApi.event('3f8b9d3a-2f0c-4f1e-9b1a-4f6a0d5e7c11')).rejects.toThrow(
      'ocorrência não encontrada',
    );
  });

  it('traduz falha de rede para linguagem do painel', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed');
      }),
    );
    await expect(publicApi.status()).rejects.toThrow('Não foi possível conectar ao UrMind.');
  });

  it('envia limite, filtros e bbox como parâmetros de consulta', async () => {
    const fetchMock = respond([]);
    await publicApi.events({
      limit: 10,
      urmind_class: 'URMIND_ROAD_D40',
      status: 'triaged',
      bbox: { south: -23.6, west: -46.7, north: -23.5, east: -46.6 },
    });
    const url = new URL(fetchMock.mock.calls[0][0], 'https://urmind.local');
    expect(url.pathname).toBe('/api/v1/public/events');
    expect(url.searchParams.get('limit')).toBe('10');
    expect(url.searchParams.get('urmind_class')).toBe('URMIND_ROAD_D40');
    expect(url.searchParams.get('status')).toBe('triaged');
    expect(url.searchParams.get('north')).toBe('-23.5');
  });

  it('omite filtros vazios para não criar consulta sem sentido', async () => {
    const fetchMock = respond([]);
    await publicApi.events({ urmind_class: '', status: '' });
    const url = new URL(fetchMock.mock.calls[0][0], 'https://urmind.local');
    expect(url.searchParams.has('urmind_class')).toBe(false);
    expect(url.searchParams.has('status')).toBe(false);
    expect(url.searchParams.get('limit')).toBe('50');
  });

  it('respeita cancelamento do chamador', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, options: RequestInit) => {
        options.signal?.throwIfAborted();
        return { ok: true, status: 200, json: async () => status };
      }),
    );
    controller.abort();
    await expect(publicApi.status(controller.signal)).rejects.toThrow();
  });
});

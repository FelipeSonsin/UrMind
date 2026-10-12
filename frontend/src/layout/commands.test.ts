import { describe, expect, it } from 'vitest';
import type { AppState } from '../app/AppState';
import type { CaptureMarker } from '../domain/contracts';
import { buildCommands, filterCommands, normalize } from './commands';

const report = (id: string, extra: Partial<CaptureMarker> = {}) =>
  ({
    id,
    latitude: -23.556,
    longitude: -46.637,
    report_status: 'processing',
    created_at: '2026-09-24T12:00:00Z',
    ...extra,
  }) as CaptureMarker;

/** Só o que a paleta lê do estado do app. */
function app(overrides: Partial<{ admin: boolean; canAdmin: boolean; session: boolean }> = {}) {
  return {
    adminSurface: overrides.admin ?? false,
    session: overrides.session === false ? null : { user: { id: 'pessoa' } },
    team: { canAdmin: overrides.canAdmin ?? false },
    drafts: { loaded: true, list: [] },
    reports: {
      own: [
        report('a', { protocol_code: 'URM-2026-00042', user_description: 'Buraco na esquina' }),
        report('b', { urmind_class: 'URMIND_ROAD_D40', severity: 'high' }),
      ],
    },
  } as unknown as AppState;
}

describe('paleta "Ir para"', () => {
  it('busca sem acento e por qualquer palavra do destino', () => {
    expect(normalize('Câmera do Robô')).toBe('camera do robo');
    // Independe da configuração do ambiente (câmera do robô pode estar desligada).
    const found = filterCommands(buildCommands(app(), 'ocorrencias'), 'ocorrencias');
    expect(found.map((command) => command.label)).toContain('Ocorrências confirmadas');
    expect(filterCommands(buildCommands(app(), ''), 'transparencia')[0].href).toBe(
      '#/transparency',
    );
  });

  it('acha o relato pelo protocolo e oferece abrir o protocolo completo', () => {
    const query = 'URM-2026-00042';
    const found = filterCommands(buildCommands(app(), query), query);
    expect(found[0]).toMatchObject({ id: 'protocol', href: '#/relato/URM-2026-00042' });
    expect(found.some((command) => command.href === '#/processando/a')).toBe(true);
  });

  it('não oferece protocolo incompleto nem protocolo sem sessão', () => {
    expect(buildCommands(app(), 'URM-2026').some((command) => command.id === 'protocol')).toBe(
      false,
    );
    expect(
      buildCommands(app({ session: false }), 'URM-2026-00042').some(
        (command) => command.id === 'protocol',
      ),
    ).toBe(false);
  });

  it('sem busca, mostra as páginas e só alguns relatos', () => {
    const shown = filterCommands(buildCommands(app(), ''), '');
    expect(shown[0].label).toBe('Registrar relato');
    expect(shown.filter((command) => command.group === 'Relatos').length).toBeLessThanOrEqual(4);
    // Rascunhos só aparecem quando existem.
    expect(shown.some((command) => command.label === 'Rascunhos')).toBe(false);
  });

  it('na área da equipe, lista só as páginas internas permitidas', () => {
    const team = buildCommands(app({ admin: true }), '').map((command) => command.label);
    expect(team).toContain('Fila de revisão');
    expect(team).not.toContain('Administração');
    expect(team).not.toContain('Registrar relato');
    expect(buildCommands(app({ admin: true, canAdmin: true }), '').map((c) => c.label)).toContain(
      'Administração',
    );
  });
});

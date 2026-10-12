import type { AppState } from '../app/AppState';
import { internalOrder, pageAvailable, privateNavigation } from '../app/routes';
import { reportPlace, reportTitle } from '../components/reports/format';

/** Um destino da paleta "Ir para…": página, relato ou consulta de protocolo. */
export interface Command {
  id: string;
  group: 'Ações' | 'Páginas' | 'Relatos';
  label: string;
  /** Texto de apoio à direita (protocolo, data, endereço). */
  hint?: string;
  /** Palavras extras para a busca (sinônimos). */
  keywords?: string;
  href: string;
}

/** Protocolo completo do relato, como `URM-7K3Q9XYZ` (sem aceitar só o começo). */
const PROTOCOL = /^[a-z]{2,5}-[a-z0-9-]{8,}$/i;

/** Sem acento e em minúsculas, para buscar "camera" e achar "Câmera". */
export function normalize(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

/**
 * Destinos da paleta para a superfície atual. Só entra o que existe de verdade: páginas
 * ligadas no registro de capacidades e relatos já carregados nesta sessão.
 */
export function buildCommands(app: AppState, query: string): Command[] {
  const commands: Command[] = [];
  const trimmed = query.trim();
  if (app.adminSurface) {
    for (const id of internalOrder) {
      if (id === 'admin' && !app.team.canAdmin) continue;
      const item = privateNavigation.find((entry) => entry.id === id)!;
      commands.push({ id, group: 'Páginas', label: item.label, href: item.href });
    }
    commands.push({ id: 'citizen', group: 'Páginas', label: 'Site do cidadão', href: '/' });
    for (const report of app.reports.own.slice(0, 50)) {
      commands.push({
        id: `report-${report.id}`,
        group: 'Relatos',
        label: report.user_description || 'Relato sem descrição',
        hint: report.protocol_code ?? undefined,
        href: `#/app/relato/${report.id}`,
      });
    }
    return commands;
  }
  if (pageAvailable('capture'))
    commands.push({
      id: 'capture',
      group: 'Ações',
      label: 'Registrar relato',
      keywords: 'foto nova enviar problema',
      href: '#/registrar',
    });
  // O protocolo abre o relato desta sessão; sem sessão, não há relato para abrir.
  if (app.session && PROTOCOL.test(trimmed))
    commands.push({
      id: 'protocol',
      group: 'Ações',
      label: `Abrir o relato ${trimmed.toUpperCase()}`,
      hint: 'protocolo',
      keywords: trimmed,
      href: `#/relato/${encodeURIComponent(trimmed.toUpperCase())}`,
    });
  const pages: Array<[string, string, string, string?]> = [
    ['overview', 'Início', '#/', 'home'],
    ['my-reports', 'Meus relatos', '#/meus-relatos', 'acompanhar protocolo situação'],
    ['map', 'Mapa', '#/mapa', 'pontos ocorrências'],
    ['events', 'Ocorrências confirmadas', '#/events', 'lista publicadas'],
    ['robot-camera', 'Câmera do robô', '#/camera-robo', 'celular transmitir quadro'],
    ['drafts', 'Rascunhos', '#/drafts', 'guardados offline'],
    ['analysis', 'Como o UrMind funciona', '#/transparency', 'transparência etapas fontes'],
    ['about', 'Sobre e privacidade', '#/sobre', 'dados lgpd'],
    ['settings', 'Situação do serviço', '#/system', 'sistema status funcionando'],
  ];
  for (const [id, label, href, keywords] of pages) {
    if (!pageAvailable(id)) continue;
    if (id === 'drafts' && !(app.drafts.loaded && app.drafts.list.length > 0)) continue;
    commands.push({ id, group: 'Páginas', label, href, keywords });
  }
  for (const report of app.reports.own.slice(0, 50)) {
    commands.push({
      id: `report-${report.id}`,
      group: 'Relatos',
      label: reportTitle(report),
      hint: report.protocol_code ?? reportPlace(report) ?? undefined,
      keywords: [report.user_description, reportPlace(report)].filter(Boolean).join(' '),
      href: `#/processando/${report.id}`,
    });
  }
  return commands;
}

/** Filtra pela busca (todas as palavras precisam aparecer); sem busca, poucos relatos. */
export function filterCommands(commands: Command[], query: string): Command[] {
  const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
  if (!terms.length) {
    let reports = 0;
    return commands.filter((command) => command.group !== 'Relatos' || reports++ < 4);
  }
  return commands
    .filter((command) => {
      const text = normalize(
        [command.label, command.hint ?? '', command.keywords ?? '', command.group].join(' '),
      );
      return terms.every((term) => text.includes(term));
    })
    .slice(0, 12);
}

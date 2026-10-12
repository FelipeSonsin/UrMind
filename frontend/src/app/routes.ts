import {
  BadgeCheck,
  Camera,
  ChartNoAxesCombined,
  ClipboardList,
  FileImage,
  House,
  LayoutDashboard,
  ListChecks,
  Map,
  MapPinned,
  ScanLine,
  ScrollText,
  Settings2,
  ShieldCheck,
  Webcam,
} from 'lucide-react';
import { activeCapabilities } from '../domain/capabilities';
import { currentSurface } from '../surface';

/**
 * Rotas do UrMind: endereços por hash (`#/mapa`, `#/events/<id>`…), sem biblioteca de
 * rotas. Para criar uma página: acrescente a entrada na lista certa abaixo, trate o
 * endereço em `parseHash` se ele tiver partes variáveis e mostre o componente em
 * `app/PageOutlet.tsx` (site do cidadão) ou `pages/team/TeamArea.tsx` (equipe).
 */

/** Páginas do site do cidadão, com o nome e o ícone usados nos menus. */
export const navigation = [
  {
    id: 'my-reports',
    label: 'Meus relatos',
    icon: ClipboardList,
    href: '#/meus-relatos',
    section: 'operação',
  },
  {
    id: 'about',
    label: 'Sobre e privacidade',
    icon: BadgeCheck,
    href: '#/sobre',
    section: 'público',
  },
  { id: 'overview', label: 'Início', icon: House, href: '#/', section: 'público' },
  { id: 'map', label: 'Mapa', icon: Map, href: '#/map', section: 'público' },
  { id: 'events', label: 'Ocorrências', icon: ClipboardList, href: '#/events', section: 'público' },
  {
    id: 'analysis',
    label: 'Como funciona',
    icon: ChartNoAxesCombined,
    href: '#/transparency',
    section: 'público',
  },
  {
    id: 'settings',
    label: 'Situação do serviço',
    icon: Settings2,
    href: '#/system',
    section: 'público',
  },
  {
    id: 'capture',
    label: 'Registrar relato',
    icon: Camera,
    href: '#/capture',
    section: 'operação',
  },
  {
    id: 'robot-camera',
    label: 'Câmera do robô',
    icon: Webcam,
    href: '#/camera-robo',
    section: 'operação',
  },
  {
    id: 'drafts',
    label: 'Rascunhos',
    icon: FileImage,
    href: '#/drafts',
    section: 'operação',
  },
  { id: 'review', label: 'Revisão', icon: ScanLine, href: '#/review', section: 'operação' },
] as const;

/** Páginas da área da equipe (/admin/). */
export const privateNavigation = [
  { id: 'dashboard', label: 'Painel', icon: LayoutDashboard, href: '#/app/dashboard' },
  { id: 'private-events', label: 'Ocorrências', icon: ClipboardList, href: '#/app/eventos' },
  { id: 'review', label: 'Fila de revisão', icon: ListChecks, href: '#/app/reviews' },
  { id: 'private-map', label: 'Mapa interno', icon: MapPinned, href: '#/app/mapa' },
  { id: 'admin', label: 'Administração', icon: ShieldCheck, href: '#/app/admin' },
  { id: 'audit', label: 'Auditoria', icon: ScrollText, href: '#/app/auditoria' },
] as const;

/** Ordem do menu da equipe; no celular, os três primeiros ficam na barra inferior. */
export const internalOrder = [
  'dashboard',
  'review',
  'private-map',
  'private-events',
  'audit',
  'admin',
];

/** Rótulos curtos da barra inferior do celular (equipe). */
export const internalShort: Record<string, string> = {
  dashboard: 'Painel',
  review: 'Fila',
  'private-map': 'Mapa',
};

/** Menu do cidadão: as três tarefas, depois as ferramentas. */
export const publicPrimary = ['overview', 'my-reports', 'map'] as const;
export const publicTools = ['robot-camera', 'drafts'] as const;

export type Page =
  | (typeof navigation)[number]['id']
  | (typeof privateNavigation)[number]['id']
  | 'event-detail'
  | 'private-detail'
  | 'private-not-found'
  | 'login'
  | 'processing'
  | 'robot-phone';

export interface Route {
  page: Page;
  eventId?: string;
  captureId?: string;
  protocol?: string;
  /** `#/resultado/<id>`: a ocorrência aberta a partir do próprio relato. */
  owner?: boolean;
  /** Endereço da área da equipe aberto no site do cidadão (cai no Início). */
  teamOnly?: boolean;
}

/** Páginas da equipe: só abrem em /admin/, nunca no site do cidadão. */
export function isTeamPage(page: Page): boolean {
  return (
    page === 'login' ||
    page === 'private-detail' ||
    page === 'private-not-found' ||
    privateNavigation.some((item) => item.id === page)
  );
}

/**
 * Rota da superfície atual. Em /admin/ só existem páginas da equipe (o resto abre o
 * painel); no site do cidadão uma rota da equipe (#/app/…, #/review, #/login) volta
 * ao Início. Assim um endereço nunca leva ao outro.
 */
export function parseRoute(): Route {
  const route = parseHash();
  if (currentSurface() === 'admin') return isTeamPage(route.page) ? route : { page: 'dashboard' };
  return isTeamPage(route.page) ? { page: 'overview', teamOnly: true } : route;
}

/** Rotas por hash: `#/`, `#/live`, `#/events`, `#/events/<id>`… sem dependência nova. */
function parseHash(): Route {
  const [, first = '', second = '', third = ''] = location.hash
    .split('?')[0]
    .replace(/^#\/?/, '/')
    .split('/');
  const path = `#/${first}`;
  if ((first === 'events' || first === 'resultado') && second)
    return { page: 'event-detail', eventId: second, ...(first === 'resultado' && { owner: true }) };
  if (first === 'processando' && second) return { page: 'processing', captureId: second };
  if (first === 'relato' && second) return { page: 'processing', protocol: second };
  if (first === 'registrar') return { page: 'capture' };
  // A antiga página do Scout (hardware fora do escopo) leva à detecção no próprio aparelho.
  if (first === 'live') return { page: 'capture' };
  // O celular abre pelo QR da Câmera do robô; o notebook fica na rota principal.
  if (first === 'camera-robo' && second === 'celular') return { page: 'robot-phone' };
  if (first === 'mapa') return { page: 'map' };
  // Os antigos exemplos revisados são as ocorrências confirmadas do próprio mapa.
  if (first === 'demo') return { page: 'map' };
  if (first === 'transparency') return { page: 'analysis' };
  if (first === 'system') return { page: 'settings' };
  if (first === 'login') return { page: 'login' };
  if (first === 'app') {
    if (!second) return { page: 'dashboard' };
    if (second === 'eventos' && third) return { page: 'private-detail', eventId: third };
    if (second === 'relato' && third) return { page: 'review', captureId: third };
    return {
      page:
        privateNavigation.find((item) => item.href === `#/app/${second}`)?.id ??
        'private-not-found',
    };
  }
  const found = navigation.find((item) => item.href === path);
  return { page: found?.id ?? 'overview' };
}

/** Páginas públicas que dependem de uma capacidade real do registro. */
export function pageAvailable(id: string): boolean {
  if (id === 'capture') return activeCapabilities.capture;
  if (id === 'robot-camera') return activeCapabilities.robotCamera;
  if (id === 'map') return activeCapabilities.map;
  return true;
}

/** Endereço de uma página do cidadão, para navegar por código. */
export function pageHref(page: Page): string {
  return navigation.find((item) => item.id === page)?.href ?? '#/';
}

/** Abre a página pública de uma ocorrência confirmada. */
export function openPublicEvent(id: string) {
  location.hash = `#/events/${id}`;
}

import { lazy } from 'react';
import { useAppState } from '../../app/AppState';
import { pageAvailable } from '../../app/routes';
import { AboutPage } from '../public/AboutPage';
import { PublicEventDetailPage } from '../public/EventDetailPage';
import { PublicEventsPage } from '../public/EventsListPage';
import { HomePage } from '../public/HomePage';
import { PublicMapPage } from '../public/MapPage';
import { SystemPage } from '../public/SystemPage';
import { TransparencyPage } from '../public/TransparencyPage';
import { MyReportsPage } from './MyReportsPage';
import { RobotCameraUnavailable } from './RobotCameraUnavailable';

// O fluxo de envio (registrar, acompanhar, rascunhos) carrega sob demanda: fica fora
// do JavaScript inicial da página de entrada (orçamento em tests/responsive.spec.ts).
const CapturePage = lazy(() => import('./CapturePage').then((m) => ({ default: m.CapturePage })));
const ReportStatusPage = lazy(() =>
  import('./ReportStatusPage').then((m) => ({ default: m.ReportStatusPage })),
);
const DraftsPage = lazy(() => import('./DraftsPage').then((m) => ({ default: m.DraftsPage })));
const RobotCameraPage = lazy(() =>
  import('./RobotCameraPage').then((m) => ({ default: m.RobotCameraPage })),
);
const RobotPhonePage = lazy(() =>
  import('./RobotCameraPage').then((m) => ({ default: m.RobotPhonePage })),
);

/** Uma página do site do cidadão por rota (lista de rotas em app/routes.ts). */
export function CitizenPage() {
  const app = useAppState();
  const { page, route, session, publicRevision, publicData, reports, drafts, navigate } = app;
  // Início e Mapa mostram todas as ocorrências: uma lista ainda filtrada (vinda da página
  // Ocorrências) não vale para eles, que esperam a lista completa chegar.
  const allEvents = publicData.eventsFilter ? null : publicData.events;
  switch (page) {
    case 'overview':
      return (
        <HomePage
          revision={publicRevision}
          events={allEvents ?? []}
          reports={reports.own}
          loaded={allEvents !== null}
          error={publicData.error}
        />
      );
    case 'map':
      return (
        <PublicMapPage
          key={session?.user.id ?? 'no-session'}
          events={allEvents ?? []}
          loaded={allEvents !== null}
          error={publicData.error}
          reports={reports.own}
        />
      );
    case 'events':
      return (
        <PublicEventsPage
          events={publicData.events ?? []}
          loaded={publicData.events !== null}
          listFilter={publicData.eventsFilter}
          error={publicData.error}
          filters={publicData.filters}
          onFilters={publicData.setFilters}
        />
      );
    case 'event-detail':
      return route.eventId ? (
        <PublicEventDetailPage
          key={`${route.eventId}:${session?.user.id ?? 'none'}`}
          id={route.eventId}
          revision={publicRevision}
          owner={route.owner}
        />
      ) : null;
    case 'processing':
      return <ReportStatusPage />;
    case 'my-reports':
      return <MyReportsPage />;
    case 'analysis':
      return <TransparencyPage revision={publicRevision} />;
    case 'about':
      return <AboutPage revision={publicRevision} />;
    case 'settings':
      return (
        <SystemPage
          status={publicData.status}
          error={publicData.statusError}
          onReload={app.refreshPublic}
        />
      );
    case 'capture':
      return (
        <CapturePage
          key={drafts.editing?.id || 'new'}
          initial={drafts.editing}
          onSaved={async (saved) => {
            drafts.setEditing(undefined);
            navigate('drafts');
            await drafts.reload();
            await drafts.send(saved);
          }}
        />
      );
    case 'robot-camera':
      return pageAvailable('robot-camera') ? (
        // Remontada por sessão, como a Detecção ao vivo.
        <RobotCameraPage
          key={session?.user.id ?? 'no-session'}
          onOpenDraft={async (draft) => {
            drafts.setEditing(draft);
            navigate('capture');
            await drafts.reload();
          }}
        />
      ) : (
        <RobotCameraUnavailable />
      );
    case 'robot-phone':
      return pageAvailable('robot-camera') ? <RobotPhonePage /> : null;
    case 'drafts':
      return <DraftsPage />;
    default:
      return null;
  }
}

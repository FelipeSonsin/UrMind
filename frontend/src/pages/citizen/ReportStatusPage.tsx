import { lazy, useEffect, useState } from 'react';
import { CloudOff, RefreshCw, SearchX, ServerOff } from 'lucide-react';
import { useOnline } from '../../app/hooks/useOnline';
import { useAppState } from '../../app/AppState';
import { ReportsError } from '../../components/reports/ReportLists';
import { AddressSearch } from '../../components/AddressSearch';
import { ReportsMap } from '../../components/reports/ReportsMap';
import { reportPlace, reportTitle } from '../../components/reports/format';
import { CopyButton, EmptyState, PageHeader, SituationTrack } from '../../components/ui';
import { publicSituation, situationLabel, type PublicSituation } from '../../domain/public';
import { api, REPORT_NOT_FOUND } from '../../services/api';

const OwnerReportTimeline = lazy(() =>
  import('../../components/EventDetail').then((m) => ({ default: m.OwnerReportTimeline })),
);

/** O que acontece agora com o relato, em uma frase, para cada situação. */
const NEXT_STEP: Record<PublicSituation, string> = {
  received: 'Uma pessoa da equipe vai conferir a foto e o local e definir o tipo de problema.',
  analyzing:
    'Uma pessoa da equipe está conferindo a foto e o local e definindo o tipo de problema.',
  confirmed: 'A equipe confirmou o problema.',
  needs_location: 'Marque no mapa, logo abaixo, onde a foto foi tirada.',
  declined: 'A equipe não confirmou um problema neste relato.',
};

/** Relato que este aparelho não tem: aviso próprio, sem trilha nem mapa. */
function NotFound({ protocol }: { protocol?: string }) {
  return (
    <>
      <PageHeader
        title="Relato não encontrado"
        back={{ href: '#/meus-relatos', label: 'Meus relatos' }}
      />
      <section className="panel">
        <EmptyState
          icon={<SearchX size={22} strokeWidth={1.8} />}
          title={
            protocol
              ? `Não encontramos o relato ${protocol} neste aparelho`
              : 'Não encontramos este relato neste aparelho'
          }
          action={
            <>
              <a className="button" href="#/meus-relatos">
                Ver meus relatos
              </a>
              <a className="button secondary" href="#/registrar">
                Registrar relato
              </a>
            </>
          }
        >
          {protocol ? 'Confira o número do protocolo. ' : ''}O relato só aparece no celular ou no
          navegador de onde foi enviado.
        </EmptyState>
      </section>
    </>
  );
}

/** Falha ao abrir o relato (rede ou servidor): diz qual relato e deixa tentar de novo. */
function OpenError({ protocol, onRetry }: { protocol?: string; onRetry: () => void }) {
  // Sem rede no aparelho, a causa é a conexão; com rede, é o serviço (não culpa a pessoa).
  const online = useOnline();
  return (
    <>
      <PageHeader title="Seu relato" back={{ href: '#/meus-relatos', label: 'Meus relatos' }} />
      <section className="panel">
        <EmptyState
          icon={
            online ? (
              <ServerOff size={22} strokeWidth={1.8} />
            ) : (
              <CloudOff size={22} strokeWidth={1.8} />
            )
          }
          title={
            protocol
              ? `Não foi possível abrir o relato ${protocol}`
              : 'Não foi possível abrir o relato'
          }
          action={
            <>
              <button type="button" onClick={onRetry}>
                Tentar de novo
              </button>
              <a className="button secondary" href="#/meus-relatos">
                Ver meus relatos
              </a>
            </>
          }
        >
          {online ? (
            <>
              O UrMind não respondeu agora. Tente de novo em alguns minutos ou veja a{' '}
              <a href="#/system">Situação do serviço</a>.
            </>
          ) : (
            'Este aparelho está sem internet. Conecte-se e tente de novo.'
          )}
        </EmptyState>
      </section>
    </>
  );
}

/**
 * Seu relato (`#/processando/<id>`): qual é o relato, a situação, o protocolo e o
 * histórico; abaixo, o relato no mapa. Sem localização, o ponto é marcado no mapa.
 * A situação vem do relato (que traz a decisão da equipe); antes de ele carregar, do
 * andamento do envio.
 */
export function ReportStatusPage() {
  const { session, sessionReady, processing, reports, revision, route, setNotice } = useAppState();
  const captureStatus = processing.status;
  const report = reports.own.find((row) => row.id === captureStatus?.capture_id);
  const status = report?.report_status ?? captureStatus?.status;
  const needsLocation = status === 'location_required';
  const sentAt = report?.created_at
    ? new Date(report.created_at).toLocaleDateString('pt-BR')
    : null;
  // Sem tipo nem descrição, o nome genérico da página diz mais que "Relato sem descrição".
  const title =
    report && (report.urmind_class || report.user_description?.trim())
      ? reportTitle(report)
      : 'Seu relato';
  // "Ver ocorrência" só com o problema confirmado: depois da primeira de duas decisões,
  // ou de uma recusa, a ocorrência ainda não é um resultado. Sem o relato carregado, vale
  // a análise concluída (fluxo antigo, com análise automática).
  // A foto e o endereço dizem de qual relato se trata (a foto só para esta sessão).
  const captureId = captureStatus?.capture_id;
  const userId = session?.user.id;
  const [photo, setPhoto] = useState<{ id: string; url: string } | null>(null);
  useEffect(() => {
    if (!userId || !captureId) return;
    const controller = new AbortController();
    api
      .captureImage(captureId, controller.signal)
      .then(({ image_url }) => {
        if (!controller.signal.aborted) setPhoto({ id: captureId, url: image_url });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [userId, captureId]);
  const photoUrl = photo && photo.id === captureId ? photo.url : null;
  // "Confirmar este local" antes de mexer no mapa: o botão diz o que falta, sem ficar cinza.
  const [askMove, setAskMove] = useState(false);
  // Endereço escolhido na busca: o mapa leva o alvo até ele e o ponto já vale.
  const [addressPoint, setAddressPoint] = useState<{ latitude: number; longitude: number } | null>(
    null,
  );
  const pointReady = Boolean(reports.manualPoint);
  if (askMove && pointReady) setAskMove(false);
  const place = report ? reportPlace(report) : null;
  // Com o tipo no título, a descrição de quem enviou vem logo abaixo.
  const described =
    report?.urmind_class && report.user_description?.trim() ? report.user_description.trim() : null;
  const eventId = report
    ? publicSituation(report.report_status) === 'confirmed'
      ? (report.event_public_id ?? undefined)
      : undefined
    : captureStatus && ['completed', 'needs_review'].includes(captureStatus.status)
      ? captureStatus.event_public_ids[0]
      : undefined;

  if (processing.notFound) return <NotFound protocol={processing.notFound} />;
  if (processing.error === REPORT_NOT_FOUND) return <NotFound />;
  // Sessão lida e nenhuma neste navegador: o relato não é deste aparelho.
  if (sessionReady && !session) return <NotFound protocol={route.protocol} />;
  if (processing.error && !captureStatus)
    return <OpenError protocol={route.protocol} onRetry={processing.retry} />;

  return (
    <>
      <PageHeader
        title={title}
        description={sentAt ? `Enviado em ${sentAt}` : undefined}
        back={{ href: '#/meus-relatos', label: 'Meus relatos' }}
      />
      <section className="panel report-status">
        {!session ? (
          <p role="status" className="loading-line">
            Abrindo o seu relato…
          </p>
        ) : null}
        {!captureStatus && session && !processing.error && (
          <div className="skeleton" aria-hidden="true">
            <span />
            <span />
          </div>
        )}
        {(photoUrl || place || described) && (
          <div className="report-summary">
            {photoUrl && <img src={photoUrl} alt="Foto enviada neste relato" />}
            <div>
              {described && <p>{described}</p>}
              {place && <p className="muted">{place}</p>}
            </div>
          </div>
        )}
        {captureStatus && status && (
          <div className="report-status-track">
            <p role="status" className="stage" data-stage={status}>
              Situação: <strong>{situationLabel(status)}</strong>
            </p>
            <SituationTrack status={status} size="large" />
            {/* Sem local, o mapa vem logo abaixo, com a única instrução. */}
            {!needsLocation && <p className="next-step">{NEXT_STEP[publicSituation(status)]}</p>}
            {/* Depois do envio, para ninguém achar que o conserto já foi pedido. */}
            <p className="report-channel">
              O UrMind não avisa a prefeitura: para pedir o conserto, registre também no{' '}
              <a
                href="https://sp156.prefeitura.sp.gov.br/portal"
                target="_blank"
                rel="noopener noreferrer"
              >
                SP156
              </a>
              .
            </p>
          </div>
        )}
        {session && captureStatus && needsLocation && (
          <div className="report-pick">
            <h2 id="report-map-title" tabIndex={-1}>
              Onde a foto foi tirada
            </h2>
            <AddressSearch
              onChoose={(point) => {
                setAddressPoint(point);
                reports.setManualPoint(point);
              }}
            />
            <ReportsMap
              onlyId={captureStatus.capture_id}
              showList={false}
              pick
              pickedPoint={addressPoint}
            />
            <button
              type="button"
              className="confirm-location"
              disabled={reports.savingLocation}
              onClick={() =>
                pointReady ? void reports.confirmMissingLocation() : setAskMove(true)
              }
            >
              {reports.savingLocation ? 'Salvando…' : 'Confirmar este local'}
            </button>
            {askMove && (
              <p className="field-error" role="alert">
                Antes, arraste o mapa até o alvo ficar sobre o local do problema.
              </p>
            )}
            <ReportsError />
          </div>
        )}
        {captureStatus?.protocol_code && (
          <div className="protocol">
            <span>Protocolo</span>
            <strong className="protocol-code" translate="no">
              {captureStatus.protocol_code}
            </strong>
            <CopyButton
              text={captureStatus.protocol_code}
              label="Copiar protocolo"
              onCopied={() => setNotice('Protocolo copiado.')}
              onFailed={() => setNotice('Selecione o protocolo e copie manualmente.')}
            />
          </div>
        )}
        {captureStatus?.model_status === 'EXPERIMENTAL_SHADOW' && (
          <p className="notice">
            Resultado automático em teste: a equipe confere antes de qualquer publicação.
          </p>
        )}
        {processing.error && (
          <p className="error" role="alert">
            {processing.error}
          </p>
        )}
        {captureStatus && (
          <OwnerReportTimeline
            key={`${session?.user.id}:${captureStatus.capture_id}`}
            id={captureStatus.capture_id}
            revision={revision}
          />
        )}
        <div className="actions">
          {eventId && (
            <a className="button" href={`#/resultado/${eventId}`}>
              Ver ocorrência
            </a>
          )}
          <button type="button" className="ghost" onClick={processing.retry}>
            <RefreshCw size={16} aria-hidden="true" /> Atualizar
          </button>
        </div>
      </section>
      {session && captureStatus && !needsLocation && (
        <section className="panel reports-panel" aria-labelledby="report-map-title">
          <h2 id="report-map-title" tabIndex={-1}>
            Seu relato no mapa
          </h2>
          <ReportsMap onlyId={captureStatus.capture_id} showList={false} />
          <ReportsError />
        </section>
      )}
    </>
  );
}

import { lazy, Suspense, useMemo } from 'react';
import { LockKeyhole, MapPinOff } from 'lucide-react';
import { useEventDetail } from '../../app/hooks/usePublicData';
import {
  ExperimentalBadge,
  LocationSummary,
  PredictionPanel,
  RecommendedAction,
  UrbanAnalysis,
} from '../../components/public/Diagnosis';
import { EmptyState, PageHeader, Skeleton, useDocumentTitle } from '../../components/ui';
import { plain } from '../../content/plainLanguage';
import { SeveritySign } from '../../components/SeveritySign';
import { labelFor, severityOf } from '../../domain/public';

// Recolhidos por padrão: carregam à parte, fora do JavaScript inicial do site.
const TechnicalDetails = lazy(() =>
  import('../../components/public/TechnicalDetails').then((m) => ({ default: m.TechnicalDetails })),
);

/**
 * Ocorrência confirmada: o que é, a foto, por que tem esta prioridade, a solução e
 * onde. Coordenadas, modelo, regras e fontes ficam nos detalhes técnicos, recolhidos.
 */
export function PublicEventDetailPage({
  id,
  revision,
  owner = false,
}: {
  id: string;
  revision: number;
  /** Aberta a partir do próprio relato: volta para "Meus relatos" e diz que é o resultado. */
  owner?: boolean;
}) {
  const back = owner
    ? { href: '#/meus-relatos', label: 'Meus relatos' }
    : { href: '#/events', label: 'Ocorrências' };
  const { data: event, error, loading, notFound } = useEventDetail(id, revision);
  const severity = useMemo(() => severityOf(event?.risk?.severity), [event]);
  const title = event ? labelFor(event.urmind_class) : null;
  useDocumentTitle(title);
  if (loading && !event) return <Skeleton lines={6} />;
  if (error && !notFound)
    return (
      <>
        <PageHeader title="Ocorrência" back={back} />
        <p className="error" role="alert">
          {error}
        </p>
      </>
    );
  if (!event)
    return (
      <>
        <PageHeader title="Ocorrência" back={back} />
        <div className="panel">
          <EmptyState
            icon={<MapPinOff size={22} strokeWidth={1.8} />}
            title="Ocorrência não encontrada"
            action={
              <a className="button secondary" href="#/mapa">
                Ver no mapa
              </a>
            }
          >
            O endereço pode estar incompleto ou a ocorrência ainda não foi publicada.
          </EmptyState>
        </div>
      </>
    );
  const date = new Date(event.occurred_at).toLocaleDateString('pt-BR');
  const photo = event.image.available && event.image.url ? event.image.url : null;
  return (
    <>
      <div className="page-heading public">
        <div className="page-heading-text">
          <a className="back-link" href={back.href}>
            <span aria-hidden="true">‹</span> {back.label}
          </a>
          <ExperimentalBadge stage={event.model_stage} />
          <h1>{title}</h1>
          {/* Toda ocorrência publicada já foi confirmada: a gravidade é o que distingue. */}
          <p className="event-meta">
            <span className={`risk-tag risk-${severity.level}`}>
              <SeveritySign level={severity.level} /> {severity.label}
            </span>
          </p>
          <p className="event-where">
            Registrada em {date}
            {owner && ', a partir do seu relato'}
          </p>
          {/* Só o dono vê uma ocorrência ainda sem confirmação: a página diz isso. */}
          {event.status !== 'confirmed' && (
            <p className="notice">
              Ainda não confirmada pela equipe: o tipo, a gravidade e a solução podem mudar.
            </p>
          )}
        </div>
      </div>

      <div className={`event-layout${photo || event.analysis ? '' : ' is-single'}`}>
        {(photo || event.analysis) && (
          <div className="event-main">
            {photo && (
              <section className="panel" aria-label="Evidência visual">
                <h2>Foto</h2>
                <figure className="evidence-figure">
                  <div className="evidence-frame">
                    <img
                      src={photo}
                      alt="Foto da ocorrência, revisada para privacidade"
                      fetchPriority="high"
                      decoding="async"
                    />
                    {event.detections.map(
                      (detection, index) =>
                        detection.bbox && (
                          <span
                            key={index}
                            className="bbox"
                            style={{
                              left: `${detection.bbox.x * 100}%`,
                              top: `${detection.bbox.y * 100}%`,
                              width: `${detection.bbox.width * 100}%`,
                              height: `${detection.bbox.height * 100}%`,
                            }}
                            title={labelFor(detection.urmind_class)}
                          />
                        ),
                    )}
                  </div>
                </figure>
              </section>
            )}
            <UrbanAnalysis event={event} />
          </div>
        )}
        <div className="event-side">
          <section className="panel" aria-label="Solução indicada">
            <RecommendedAction event={event} heading="h2" channel />
          </section>
          <section className="panel" aria-label="Local">
            <LocationSummary event={event} />
          </section>
          {event.prediction.available && (
            <section className="panel" aria-label="Previsão">
              <PredictionPanel event={event} />
            </section>
          )}
        </div>
      </div>

      {!photo && (
        // Sem foto pública: uma linha discreta com o motivo, depois do que importa.
        <section className="photo-note" aria-label="Evidência visual">
          <LockKeyhole size={18} strokeWidth={1.8} aria-hidden="true" />
          <p>
            <strong>Sem foto publicada.</strong>{' '}
            {plain(event.image.reason ?? 'A foto desta ocorrência não está publicada.')}
          </p>
        </section>
      )}
      <Suspense fallback={null}>
        <TechnicalDetails event={event} />
      </Suspense>
    </>
  );
}

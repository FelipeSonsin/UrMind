import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { usePublicData } from '../../app/hooks/usePublicData';
import { ExperimentalBadge } from '../../components/public/Diagnosis';
import { OwnReportDetail } from '../../components/reports/OwnReportDetail';
import { PageHeader, SeverityTag } from '../../components/ui';
import type { CaptureMarker } from '../../domain/contracts';
import { labelFor, type PublicEvent, type PublicEventDetail } from '../../domain/public';
import { api } from '../../services/api';
import { publicApi } from '../../services/publicApi';

const UrbanMap = lazy(() => import('../../components/UrbanMap'));

type MapKind = 'all' | 'mine' | 'published';
const MAP_KINDS: [MapKind, string][] = [
  ['all', 'Todos'],
  ['mine', 'Meus relatos'],
  ['published', 'Confirmados'],
];

/**
 * Mapa público: ocorrências confirmadas, os relatos da própria sessão e, sem detalhe,
 * os pontos ainda em análise de outras pessoas. `?ponto=<id>` abre um ponto e
 * `?mostrar=mine|published` escolhe a camada.
 */
export function PublicMapPage({
  events,
  loaded = true,
  error = '',
  reports = [],
}: {
  events: PublicEvent[];
  /** As ocorrências já chegaram uma vez: antes disso, nada de "nenhuma ocorrência". */
  loaded?: boolean;
  error?: string;
  reports?: CaptureMarker[];
}) {
  const query = () => new URLSearchParams(location.hash.split('?')[1] ?? '');
  const [selected, setSelected] = useState<string | null>(() => query().get('ponto'));
  const [kind, setKind] = useState<MapKind>(() => {
    // O antigo endereço de exemplos revisados abre direto nas ocorrências confirmadas.
    if (location.hash.startsWith('#/demo')) return 'published';
    const requested = query().get('mostrar');
    return MAP_KINDS.some(([value]) => value === requested) ? (requested as MapKind) : 'all';
  });
  const [pointDetail, setPointDetail] = useState<PublicEventDetail | null>(null);
  const [pointError, setPointError] = useState('');
  const [photo, setPhoto] = useState<{ id: string; url: string } | null>(null);
  const { data: generic } = usePublicData((signal) => publicApi.captureMarkers(signal), []);
  const own = reports.find((report) => report.id === selected) ?? null;
  const isGeneric = Boolean(selected && generic?.some((marker) => marker.id === selected));
  useEffect(() => {
    const controller = new AbortController();
    setPointDetail(null);
    setPointError('');
    // A deep link may refer to a publication newer than the cached map page,
    // or outside its bounded listing. The publication endpoint is the authority.
    // Own reports and other people's generic markers are not publications.
    if (selected && !own && !isGeneric) {
      publicApi
        .publishedEvent(selected, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setPointDetail(value);
        })
        .catch(() => {
          if (!controller.signal.aborted)
            setPointError('Não foi possível abrir este ponto agora. Tente de novo em instantes.');
        });
    }
    return () => controller.abort();
  }, [selected, own, isGeneric]);
  // The author's own original photo: private, served only to the owner's session.
  const ownId = own?.id;
  useEffect(() => {
    const controller = new AbortController();
    setPhoto(null);
    if (ownId)
      api
        .captureImage(ownId, controller.signal)
        .then(({ image_url }) => {
          if (!controller.signal.aborted) setPhoto({ id: ownId, url: image_url });
        })
        .catch(() => undefined);
    return () => controller.abort();
  }, [ownId]);
  const markers = useMemo(() => {
    if (kind === 'mine' && reports.length) return reports;
    if (kind === 'published' && events.length) return events;
    const reportIds = new Set(reports.map((report) => report.public_id));
    const eventIds = new Set(events.map((event) => event.id));
    return [
      ...events,
      ...(generic ?? []).filter((report) => !reportIds.has(report.id)),
      ...reports.filter(
        (report) => !report.event_public_id || !eventIds.has(report.event_public_id),
      ),
    ];
  }, [events, reports, generic, kind]);
  // "Meus relatos" e "Confirmados" só aparecem quando têm pontos e diferem de "Tudo".
  const located = (rows: Array<{ latitude?: number | null; longitude?: number | null }>) =>
    rows.filter((row) => row.latitude != null && row.longitude != null).length;
  const everything = located([...events, ...(generic ?? []), ...reports]);
  const views = MAP_KINDS.filter(([value]) => {
    if (value === 'all') return true;
    const count = located(value === 'mine' ? reports : events);
    return count > 0 && count < everything;
  });
  const shownKind: MapKind = views.some(([value]) => value === kind) ? kind : 'all';
  function changeKind(next: MapKind) {
    setKind(next);
    setSelected(null);
    const params = query();
    params.delete('ponto');
    if (next === 'all') params.delete('mostrar');
    else params.set('mostrar', next);
    history.replaceState(null, '', `#/mapa${params.size ? `?${params}` : ''}`);
  }
  return (
    <>
      <PageHeader
        title="Mapa"
        description="Ocorrências confirmadas pela equipe e os relatos que você enviou. Escolha um ponto para ver os detalhes."
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {views.length > 1 && (
        <div className="segmented" role="radiogroup" aria-label="Mostrar no mapa">
          {views.map(([value, label]) => (
            <label key={value} className={shownKind === value ? 'active' : undefined}>
              <input
                type="radio"
                name="map-kind"
                value={value}
                checked={shownKind === value}
                onChange={() => changeKind(value)}
              />
              {label}
            </label>
          ))}
        </div>
      )}
      <Suspense fallback={<p role="status">Carregando mapa…</p>}>
        <UrbanMap
          key={shownKind}
          events={markers}
          selectedId={selected}
          emptyMessage={
            shownKind === 'mine'
              ? 'Seus relatos com localização aparecem aqui.'
              : loaded
                ? 'Nenhuma ocorrência publicada ainda.'
                : error
                  ? null
                  : 'Carregando ocorrências…'
          }
          audience="public"
          onSelect={(id) => {
            setSelected(id);
            if (events.some((event) => event.id === id)) {
              const params = query();
              params.set('ponto', id);
              history.replaceState(null, '', `#/mapa?${params}`);
            }
          }}
          onCloseDetail={() => setSelected(null)}
          detail={
            selected &&
            (own ? (
              <OwnReportDetail report={own} photoUrl={photo?.id === own.id ? photo.url : null}>
                <a href={`#/processando/${own.id}`}>Acompanhar relato</a>
              </OwnReportDetail>
            ) : isGeneric ? (
              <p>Relato de outra pessoa, ainda em análise. Foto e descrição não são públicas.</p>
            ) : (
              <>
                {pointError && (
                  <p className="error" role="alert">
                    {pointError}
                  </p>
                )}
                {pointDetail && pointDetail.id === selected ? (
                  <>
                    <h2>{labelFor(pointDetail.urmind_class)}</h2>
                    <p>
                      <SeverityTag severity={pointDetail.risk?.severity ?? pointDetail.severity} />
                    </p>
                    <ExperimentalBadge stage={pointDetail.model_stage} />
                    {pointDetail.image.available && pointDetail.image.url && (
                      <img
                        src={pointDetail.image.url}
                        alt="Foto publicada da ocorrência, revisada para privacidade"
                      />
                    )}
                    {pointDetail.road_name && <p>{pointDetail.road_name}</p>}
                    {pointDetail.latitude != null && pointDetail.longitude != null && (
                      <a
                        href={`https://www.google.com/maps/dir/?api=1&destination=${pointDetail.latitude.toFixed(4)},${pointDetail.longitude.toFixed(4)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Como chegar
                      </a>
                    )}
                    <a href={`#/resultado/${pointDetail.id}`}>Ver ocorrência</a>
                  </>
                ) : (
                  !pointError && (
                    <p role="status" className="loading-line">
                      Carregando detalhe…
                    </p>
                  )
                )}
              </>
            ))
          }
        />
      </Suspense>
    </>
  );
}

import { lazy, Suspense, useState } from 'react';
import { Camera } from 'lucide-react';
import { useAppState } from '../../app/AppState';
import { useEventDetail } from '../../app/hooks/usePublicData';
import { QuickDiagnosis, RecommendedAction } from '../../components/public/Diagnosis';
import { CitizenReportList } from '../../components/reports/ReportLists';
import { Skeleton } from '../../components/ui';
import { activeCapabilities } from '../../domain/capabilities';
import type { CaptureMarker } from '../../domain/contracts';
import type { PublicEvent } from '../../domain/public';

const UrbanMap = lazy(() => import('../../components/UrbanMap'));

/** Início: registrar, acompanhar os próprios relatos e ver as ocorrências confirmadas. */
export function HomePage({
  revision,
  events,
  reports,
  loaded,
  error,
}: {
  revision: number;
  events: PublicEvent[];
  reports: CaptureMarker[];
  /** As ocorrências já chegaram uma vez: antes disso, nada de "nenhuma ocorrência". */
  loaded: boolean;
  error: string;
}) {
  const { route } = useAppState();
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? events[0]?.id ?? null;
  const {
    data: detail,
    loading: detailLoading,
    error: detailError,
  } = useEventDetail(current, revision);
  return (
    <>
      {/* Endereço da área da equipe aberto aqui: o Início diz por que abriu. */}
      {route.teamOnly && (
        <p className="notice" role="status">
          Esta página é da área da equipe. <a href="/admin/">Entrar na área da equipe</a>
        </p>
      )}
      <section className="home-hero" aria-labelledby="home-title">
        <div className="home-intro">
          {/* Duas frases, duas linhas: a quebra nunca cai no meio de uma delas. */}
          <h1 id="home-title">
            <span>Viu um problema na rua?</span> <span>Registre com uma foto.</span>
          </h1>
          <p>Buracos, calçadas quebradas, postes apagados: mande uma foto do lugar.</p>
          <div className="actions">
            {activeCapabilities.capture && (
              <a className="button large" href="#/registrar">
                <Camera size={18} aria-hidden="true" /> Registrar relato
              </a>
            )}
          </div>
        </div>
        {/* A sequência real de um relato: por isso numerada. */}
        <ol className="home-steps" aria-label="Como funciona">
          <li>
            <strong>Foto e local</strong>
            <span>Tire a foto no lugar. O local vem do GPS do aparelho ou da própria foto.</span>
          </li>
          <li>
            <strong>Revisão da equipe</strong>
            <span>
              Uma pessoa confere a foto e o local, define o tipo de problema e vê se não há rostos
              ou placas de veículos à mostra.
            </span>
          </li>
          <li>
            <strong>Mapa público</strong>
            <span>O que for confirmado aparece no mapa, com tipo e gravidade.</span>
          </li>
        </ol>
      </section>
      {/* Só quem já enviou vê a seção: para quem chega, o botão acima basta. */}
      {reports.length > 0 && (
        <section className="home-reports" aria-labelledby="home-reports-title">
          <div className="section-heading home-section">
            <h2 id="home-reports-title">Meus relatos</h2>
            {/* O link só aparece quando há mais relatos do que os três daqui. */}
            {reports.length > 3 && (
              <a className="text-button" href="#/meus-relatos">
                Ver todos ({reports.length})
              </a>
            )}
          </div>
          <div className="panel">
            <CitizenReportList reports={reports.slice(0, 3)} linkTitles />
          </div>
        </section>
      )}
      <div className="section-heading home-section">
        <h2>Ocorrências confirmadas</h2>
        <a className="text-button" href="#/map">
          Abrir mapa completo
        </a>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {/* Sem ocorrência publicada, o mapa ocupa a linha toda e avisa uma vez só. */}
      <div className={`home-lead${events.length ? '' : ' is-empty'}`}>
        <Suspense fallback={<p role="status">Carregando mapa…</p>}>
          <UrbanMap
            events={events}
            // Só uma escolha explícita leva o mapa até o ponto; de início ele enquadra todos.
            selectedId={selected}
            onSelect={setSelected}
            showFilters={false}
            emptyMessage={
              loaded
                ? 'Nenhuma ocorrência publicada ainda.'
                : error
                  ? null
                  : 'Carregando ocorrências…'
            }
          />
        </Suspense>
        {/* Painel só com ocorrência: nas releituras (a cada 30 s) nada aparece e some. */}
        {events.length > 0 && (
          <section className="panel diagnosis" aria-label="Diagnóstico da ocorrência selecionada">
            {/* Há ocorrência: sem o detalhe ainda, esqueleto (nunca "nenhuma ocorrência"). */}
            {detailError && !detailLoading ? (
              <p className="muted">
                Não foi possível abrir esta ocorrência agora. A página tenta de novo sozinha em
                instantes.
              </p>
            ) : detail ? (
              <>
                <QuickDiagnosis
                  event={detail}
                  label={selected ? 'Ocorrência selecionada' : 'Mais recente'}
                />
                <RecommendedAction event={detail} />
                <a className="button secondary" href={`#/events/${detail.id}`}>
                  Ver ocorrência
                </a>
              </>
            ) : (
              <Skeleton lines={5} />
            )}
          </section>
        )}
      </div>
    </>
  );
}

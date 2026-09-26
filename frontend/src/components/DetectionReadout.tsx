import type { Metrics } from '../pages/useVideoDetection';

const decimal = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

/**
 * Desempenho medido do vídeo e da análise (detalhes técnicos): quadros apresentados
 * por segundo, análises por segundo, latência e resolução. Número medido, nunca meta.
 */
export function DetectionReadout({
  metrics,
  live,
  declaredFps = null,
  resolution,
  frameId,
  videoLabel = 'Câmera',
}: {
  metrics: Metrics;
  live: boolean;
  declaredFps?: number | null;
  resolution: string | null;
  frameId: number | null;
  videoLabel?: string;
}) {
  return (
    <dl className="live-readout" aria-label="Desempenho medido">
      <div>
        {/* Sem requestVideoFrameCallback só existe o valor declarado pelo dispositivo. */}
        <dt>
          {metrics.cameraFps == null && live && declaredFps
            ? `${videoLabel} (declarado)`
            : videoLabel}
        </dt>
        <dd>
          {metrics.cameraFps != null
            ? `${decimal.format(metrics.cameraFps)} fps`
            : live && declaredFps
              ? `${decimal.format(declaredFps)} fps`
              : '—'}
        </dd>
      </div>
      <div>
        <dt>Análises</dt>
        <dd>{metrics.inferenceFps != null ? `${decimal.format(metrics.inferenceFps)} /s` : '—'}</dd>
      </div>
      <div>
        <dt>Intervalo</dt>
        <dd>{metrics.intervalMs != null ? `${Math.round(metrics.intervalMs)} ms` : '—'}</dd>
      </div>
      <div>
        <dt>Latência</dt>
        <dd>{metrics.latencyMs != null ? `${Math.round(metrics.latencyMs)} ms` : '—'}</dd>
      </div>
      <div>
        <dt>Latência p50 / p95</dt>
        <dd>
          {metrics.latencyP50 != null && metrics.latencyP95 != null
            ? `${Math.round(metrics.latencyP50)} / ${Math.round(metrics.latencyP95)} ms`
            : '—'}
        </dd>
      </div>
      <div>
        <dt>Pré · modelo · pós</dt>
        <dd>
          {metrics.stages
            ? `${Math.round(metrics.stages.preprocess)} · ${Math.round(metrics.stages.inference)} · ${Math.round(metrics.stages.postprocess)} ms`
            : '—'}
        </dd>
      </div>
      <div>
        <dt>Resolução</dt>
        <dd>{live && resolution ? resolution : '—'}</dd>
      </div>
      <div>
        <dt>Quadro</dt>
        <dd>{frameId != null ? `nº ${frameId}` : '—'}</dd>
      </div>
    </dl>
  );
}

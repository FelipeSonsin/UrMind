import { useEffect, useRef, useState } from 'react';
import { labelFor, type PublicEventDetail, type PublicScout } from '../../domain/public';

type Connection = 'CONNECTING' | 'LIVE' | 'DEGRADED' | 'OFFLINE' | 'NO_CAMERA';

const SNAPSHOT_INTERVAL_MS = 2000;

/**
 * Fonte de vídeo do Scout. Hoje existem dois adaptadores reais: stream HTTP
 * (multipart/MJPEG) e snapshots. WebRTC entra como terceiro adaptador quando houver
 * necessidade comprovada — a interface aqui é o ponto de extensão, não uma promessa.
 */
export interface VideoSourceAdapter {
  kind: 'http_stream' | 'snapshot';
  label: string;
  url: string;
}

export function adapterFor(scout: PublicScout | null): VideoSourceAdapter | null {
  if (!scout) return null;
  if (scout.camera.mode === 'live_video' && scout.camera.stream_url)
    return { kind: 'http_stream', label: 'VÍDEO AO VIVO', url: scout.camera.stream_url };
  if (scout.camera.mode === 'live_snapshots' && scout.camera.frame_url)
    return { kind: 'snapshot', label: 'IMAGENS AO VIVO', url: scout.camera.frame_url };
  return null;
}

export function connectionOf(
  scout: PublicScout | null,
  loaded: boolean,
  failed: boolean,
): Connection {
  if (!scout || scout.camera.mode === 'unavailable') return 'NO_CAMERA';
  if (failed) return scout.status === 'offline' ? 'OFFLINE' : 'DEGRADED';
  return loaded ? 'LIVE' : 'CONNECTING';
}

const CONNECTION_TEXT: Record<Connection, string> = {
  CONNECTING: 'Conectando…',
  LIVE: 'Ao vivo',
  DEGRADED: 'Sinal degradado',
  OFFLINE: 'Scout offline',
  NO_CAMERA: 'Sem câmera conectada',
};

/** Caixas reais da detecção sobre o frame; nada é desenhado sem Detection. */
function DetectionOverlay({ detections }: { detections: PublicEventDetail['detections'] }) {
  const boxes = detections.filter((item) => item.bbox);
  if (!boxes.length) return null;
  return (
    <svg className="overlay" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      {boxes.map((item, index) => (
        <g key={`${item.urmind_class}-${index}`}>
          <rect
            x={item.bbox!.x * 100}
            y={item.bbox!.y * 100}
            width={item.bbox!.width * 100}
            height={item.bbox!.height * 100}
            className="overlay-box"
            vectorEffect="non-scaling-stroke"
          />
        </g>
      ))}
    </svg>
  );
}

export function ScoutLivePanel({
  scout,
  latest,
  compact = false,
}: {
  scout: PublicScout | null;
  latest?: PublicEventDetail | null;
  compact?: boolean;
}) {
  const adapter = adapterFor(scout);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [frameAt, setFrameAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const image = useRef<HTMLImageElement>(null);

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
    if (adapter?.kind !== 'snapshot') return;
    const timer = window.setInterval(() => setTick((value) => value + 1), SNAPSHOT_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [adapter?.kind, adapter?.url]);

  const connection = connectionOf(scout, loaded, failed);
  const source = adapter
    ? adapter.kind === 'snapshot'
      ? `${adapter.url}?t=${tick}`
      : adapter.url
    : null;

  return (
    <section
      className={`panel scout-panel${compact ? ' compact' : ''}`}
      aria-label="Câmera do Scout"
    >
      <header className="scout-head">
        <h2>Scout</h2>
        <span className={`live-badge ${connection.toLowerCase()}`}>
          <i aria-hidden="true" />
          {CONNECTION_TEXT[connection]}
          {adapter && connection === 'LIVE' && <small>{adapter.label}</small>}
        </span>
      </header>
      <div className="scout-frame">
        {source ? (
          <>
            <img
              ref={image}
              src={source}
              alt="Transmissão da câmera do Scout"
              onLoad={() => {
                setLoaded(true);
                setFailed(false);
                setFrameAt(new Date());
              }}
              onError={() => setFailed(true)}
            />
            {latest && <DetectionOverlay detections={latest.detections} />}
          </>
        ) : (
          <div className="scout-empty">
            <strong>Câmera do Scout indisponível</strong>
            <p>{scout?.camera.reason ?? 'Nenhuma fonte de vídeo conectada a este ambiente.'}</p>
            <p className="muted">
              O restante do painel continua funcionando com as ocorrências já registradas.
            </p>
          </div>
        )}
      </div>
      <dl className="scout-meta">
        <div>
          <dt>Estado</dt>
          <dd>{CONNECTION_TEXT[connection]}</dd>
        </div>
        <div>
          <dt>Último frame</dt>
          <dd>{frameAt ? frameAt.toLocaleTimeString('pt-BR') : 'não disponível'}</dd>
        </div>
        <div>
          <dt>Última captura registrada</dt>
          <dd>
            {scout?.last_seen
              ? new Date(scout.last_seen).toLocaleString('pt-BR')
              : 'não disponível'}
          </dd>
        </div>
        <div>
          <dt>Latência</dt>
          <dd>
            {scout?.camera.latency_ms != null ? `${scout.camera.latency_ms} ms` : 'não medida'}
          </dd>
        </div>
      </dl>
      {latest && (
        <p className="muted">
          Caixas exibidas: detecção real de {labelFor(latest.urmind_class)} na última ocorrência
          publicada.
        </p>
      )}
    </section>
  );
}

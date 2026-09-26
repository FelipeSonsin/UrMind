import { useEffect, useRef, useState } from 'react';
import { Camera as CameraIcon, CircleStop, ImageUp, Pause, Play, Send } from 'lucide-react';
import { classColor, qualityHintText, type BrowserModelManifest } from '../domain/liveDetection';
import { labelFor } from '../domain/public';
import {
  CameraError,
  frameToJpeg,
  listCameras,
  openCamera,
  stopStream,
} from '../services/cameraStream';
import { stopWarmUp, warmUp, warmUpIfAllowed } from '../services/deviceLocation';
import { drafts, validatePhoto, type CaptureDraft } from '../services/drafts';
import { DetectionReadout } from '../components/DetectionReadout';
import { useDetectionOverlay, useElementSize, useVideoDetection } from './useVideoDetection';

type CameraState =
  | { status: 'idle' }
  | { status: 'starting' }
  | { status: 'live'; width: number; height: number; mirrored: boolean; declaredFps: number | null }
  | { status: 'suspended'; message: string }
  | { status: 'error'; message: string };

const statusLabels: Record<BrowserModelManifest['scientific_status'], string> = {
  APPROVED: 'Aprovado',
  EXPERIMENTAL: 'Experimental',
  DEMONSTRATION: 'Demonstração',
  REJECTED: 'Rejeitado',
};

const scoreFormat = new Intl.NumberFormat('pt-BR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function LiveDetectionPage({
  onOpenDraft,
}: {
  onOpenDraft: (draft: CaptureDraft) => void | Promise<void>;
}) {
  const [camera, setCamera] = useState<CameraState>({ status: 'idle' });
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  const [selected, setSelected] = useState('');
  // Camada temporal só de apresentação, reversível: desligada, tudo é desenhado igual.
  const [stabilize, setStabilize] = useState(true);
  const [captureError, setCaptureError] = useState('');
  const [capturing, setCapturing] = useState(false);
  // Imagem do aparelho: só prévia visual, sem localização, sem Capture.
  const [still, setStill] = useState<{ name: string } | null>(null);

  const video = useRef<HTMLVideoElement>(null);
  const stillInput = useRef<HTMLInputElement>(null);
  const frameCanvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const cameraToken = useRef(0);
  const cameraRef = useRef<CameraState>(camera);
  cameraRef.current = camera;

  const detection = useVideoDetection({
    video,
    stillCanvas: frameCanvas,
    source: () => {
      const current = cameraRef.current;
      return {
        live: current.status === 'live',
        mirrored: current.status === 'live' && current.mirrored,
      };
    },
    measureCamera: camera.status === 'live',
  });
  const { model, detecting, analyzed, metrics, inferenceError, setInferenceError } = detection;
  const { startDetection, stopDetection } = detection;
  const stageSize = useElementSize(stage);
  useDetectionOverlay(overlay, stageSize, analyzed, model, stabilize);

  function stopCamera(next: CameraState = { status: 'idle' }) {
    cameraToken.current += 1;
    stopDetection();
    stopStream(stream.current);
    stream.current = null;
    if (video.current) video.current.srcObject = null;
    detection.clear();
    detection.resetMetrics();
    setStill(null);
    setCamera(next);
  }

  async function analyzeStill(file: File) {
    if (!('manifest' in model)) return;
    stopCamera();
    setInferenceError('');
    setCaptureError('');
    let bitmap: ImageBitmap;
    try {
      // Orientação EXIF aplicada; os metadados não são usados como localização aqui.
      bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch {
      setInferenceError('Não foi possível ler esta imagem. Use JPEG, PNG ou WebP.');
      return;
    }
    setStill({ name: file.name });
    detection.analyzeStill(bitmap);
    bitmap.close();
  }

  async function refreshCameras() {
    try {
      setCameras(await listCameras());
    } catch {
      setCameras([]);
    }
  }

  /**
   * Abre a câmera; com `detect`, a detecção começa assim que o vídeo estiver no palco
   * (quando há modelo). Uma ação só: Iniciar.
   */
  async function startCamera(deviceId = selected, detect = false) {
    stopCamera({ status: 'starting' });
    const token = cameraToken.current;
    setInferenceError('');
    try {
      const opened = await openCamera(deviceId || undefined);
      if (token !== cameraToken.current) {
        stopStream(opened);
        return;
      }
      stream.current = opened;
      const track = opened.getVideoTracks()[0];
      const settings = track?.getSettings() ?? {};
      track?.addEventListener('ended', () => {
        if (token === cameraToken.current)
          stopCamera({
            status: 'error',
            message: 'A câmera foi desconectada. Reconecte-a e toque em Iniciar.',
          });
      });
      const player = video.current;
      if (!player) throw new Error('Área de vídeo indisponível.');
      player.srcObject = opened;
      await player.play();
      if (token !== cameraToken.current) return;
      const liveState: CameraState = {
        status: 'live',
        width: player.videoWidth || settings.width || 0,
        height: player.videoHeight || settings.height || 0,
        mirrored: settings.facingMode === 'user',
        declaredFps: settings.frameRate ?? null,
      };
      cameraRef.current = liveState;
      setCamera(liveState);
      if (detect) startDetection();
      void refreshCameras();
    } catch (reason) {
      if (token !== cameraToken.current) return;
      stopStream(stream.current);
      stream.current = null;
      if (video.current) video.current.srcObject = null;
      setCamera({
        status: 'error',
        message:
          reason instanceof CameraError
            ? reason.message
            : reason instanceof Error && reason.name === 'NotAllowedError'
              ? 'O navegador bloqueou a reprodução da câmera.'
              : 'Não foi possível abrir a câmera.',
      });
    }
  }

  function start() {
    if (cameraRef.current.status === 'live') startDetection();
    else void startCamera(selected, true);
  }

  async function captureAndRegister() {
    setCaptureError('');
    setCapturing(true);
    // O registro pede o GPS do aparelho logo em seguida; começa a procurar já.
    warmUp();
    try {
      const player = video.current;
      if (!player?.videoWidth) throw new Error('Não há quadro para capturar. Inicie a câmera.');
      // O quadro que está na tela agora, direto do vídeo: as caixas ficam no overlay
      // e nunca entram na foto registrada.
      const capturedAt = Date.now();
      const file = await frameToJpeg(
        player,
        player.videoWidth,
        player.videoHeight,
        `deteccao-ao-vivo-${capturedAt}.jpg`,
        capturedAt,
      );
      await validatePhoto(file);
      const draft: CaptureDraft = {
        id: crypto.randomUUID(),
        photo: file,
        filename: file.name,
        source: 'pwa_photo',
        captured_at: new Date(capturedAt).toISOString(),
        created_at: new Date().toISOString(),
        // O registro obtém o GPS deste instante; nada é inventado aqui.
        coordinate: null,
        source_location: 'unknown',
        location_timestamp: null,
        heading_deg: null,
        speed_mps: null,
        note: '',
        status: 'local_draft',
      };
      await drafts.save(draft);
      stopCamera();
      await onOpenDraft(draft);
    } catch (reason) {
      setCaptureError((reason as Error).message);
    } finally {
      setCapturing(false);
    }
  }

  useEffect(() => {
    void refreshCameras();
  }, []);

  // Saída da rota ou logout (a página é remontada por sessão): a câmera não continua viva.
  useEffect(
    () => () => {
      cameraToken.current += 1;
      stopStream(stream.current);
      stream.current = null;
    },
    [],
  );

  // Com permissão já concedida, o GPS se estabiliza enquanto a câmera está aberta, e
  // "Capturar e registrar" encontra uma posição recente. Sem permissão, nada é pedido aqui.
  useEffect(() => {
    void warmUpIfAllowed();
    return stopWarmUp;
  }, []);

  // Página oculta: a inferência para e a câmera é desligada; a retomada é sempre explícita.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden && stream.current)
        stopCamera({
          status: 'suspended',
          message:
            'A câmera foi desligada porque a página ficou oculta. Inicie de novo para continuar.',
        });
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const live = camera.status === 'live';
  const hasManifest = 'manifest' in model;
  const manifest = hasManifest ? model.manifest : null;
  // Só uma imagem avulsa ocupa o palco no lugar do vídeo; a câmera nunca é substituída.
  const showStill = still !== null && analyzed !== null;
  const limit = manifest?.postprocess.max_detections ?? 0;
  const aspect = showStill
    ? `${analyzed.width} / ${analyzed.height}`
    : live && camera.width && camera.height
      ? `${camera.width} / ${camera.height}`
      : '16 / 9';

  const canStart =
    camera.status !== 'starting' && !capturing && (!live || (hasManifest && !detecting));
  const hints = [
    camera.status === 'starting' && 'Abrindo a câmera…',
    live &&
      !hasManifest &&
      'Detecção indisponível: a câmera ainda pode capturar e registrar a foto.',
    still &&
      'Capturar e registrar vale só para a câmera. Para registrar esta imagem, use Registrar evidência.',
  ].filter(Boolean) as string[];

  // Para quem usa: pronta ou indisponível. O provedor (WebGPU/WASM) fica nos detalhes.
  const detectionStatus =
    model.status === 'checking'
      ? 'Verificando a detecção…'
      : model.status === 'unavailable'
        ? `Detecção indisponível. ${model.reason}`
        : model.status === 'loading'
          ? 'Preparando a detecção…'
          : model.status === 'failed'
            ? `Detecção indisponível. ${model.message}`
            : model.status === 'ready'
              ? 'Detecção pronta'
              : 'Detecção disponível: começa quando você tocar em Iniciar.';

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Detecção ao vivo</h1>
          <p>
            Aponte a câmera para a via. A análise acontece neste aparelho e nada é enviado até você
            registrar.
          </p>
        </div>
      </div>

      <div className="live-grid">
        <section className="live-stage-panel" aria-label="Imagem da câmera">
          <div
            className={`live-stage${showStill ? ' is-still' : ''}`}
            style={{ aspectRatio: aspect }}
          >
            <div className="live-frame" ref={stage}>
              <video
                ref={video}
                className={`live-video${live && camera.mirrored ? ' mirrored' : ''}`}
                playsInline
                muted
                aria-label="Imagem ao vivo da câmera"
              />
              <canvas
                ref={frameCanvas}
                className="live-analyzed"
                aria-label="Imagem analisada"
                hidden={!showStill}
              />
              <canvas ref={overlay} className="live-overlay" aria-hidden="true" />
              {!live && !showStill && (
                <div className="live-placeholder">
                  <CameraIcon size={28} strokeWidth={1.4} aria-hidden="true" />
                  <p>
                    {camera.status === 'starting'
                      ? 'Abrindo a câmera…'
                      : camera.status === 'error' || camera.status === 'suspended'
                        ? camera.message
                        : 'Toque em Iniciar para abrir a câmera.'}
                  </p>
                </div>
              )}
            </div>
            {showStill && (
              <span className="live-stage-tag">Imagem do aparelho · prévia sem localização</span>
            )}
            {live && detecting && <span className="live-stage-tag">Detectando</span>}
          </div>
          {analyzed && analyzed.hints.length > 0 && (
            <ul className="live-quality" aria-label="Qualidade da captura">
              {analyzed.hints.map((hint) => (
                <li key={hint}>{qualityHintText[hint]}</li>
              ))}
            </ul>
          )}
          <div className="live-actions" role="toolbar" aria-label="Controles da detecção">
            <button
              type="button"
              disabled={!canStart}
              onClick={start}
              aria-describedby={hints.length ? 'live-hints' : undefined}
            >
              <Play size={16} aria-hidden="true" /> Iniciar
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!detecting}
              onClick={stopDetection}
            >
              <Pause size={16} aria-hidden="true" /> Pausar
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!live && camera.status !== 'starting'}
              onClick={() => stopCamera()}
            >
              <CircleStop size={16} aria-hidden="true" /> Encerrar
            </button>
            <button
              type="button"
              className="secondary"
              disabled={capturing || still !== null || !live}
              onClick={() => void captureAndRegister()}
              aria-describedby={hints.length ? 'live-hints' : undefined}
            >
              <Send size={16} aria-hidden="true" />{' '}
              {capturing ? 'Capturando…' : 'Capturar e registrar'}
            </button>
            <button
              type="button"
              className="ghost"
              disabled={!hasManifest || capturing || model.status === 'loading'}
              onClick={() => stillInput.current?.click()}
            >
              <ImageUp size={16} aria-hidden="true" /> Analisar imagem
            </button>
            <input
              ref={stillInput}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              hidden
              aria-label="Escolher imagem para prévia"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void analyzeStill(file);
              }}
            />
          </div>
        </section>

        <div className="live-side">
          <section className="panel" aria-label="Controle da câmera">
            <p
              role="status"
              className={`live-status${model.status === 'ready' ? ' is-ready' : ''}${
                model.status === 'failed' || model.status === 'unavailable' ? ' is-error' : ''
              }`}
            >
              {detectionStatus}
            </p>
            {cameras.length > 1 && (
              <label>
                Câmera do aparelho
                <select
                  value={selected}
                  disabled={camera.status === 'starting' || capturing}
                  onChange={(event) => {
                    setSelected(event.target.value);
                    if (live) void startCamera(event.target.value, detecting);
                  }}
                >
                  <option value="">Automática (traseira quando houver)</option>
                  {cameras.map((device, index) => (
                    <option key={device.deviceId || index} value={device.deviceId}>
                      {device.label || `Câmera ${index + 1}`}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {hints.length > 0 && (
              <ul id="live-hints" className="live-hints">
                {hints.map((hint) => (
                  <li key={hint}>{hint}</li>
                ))}
              </ul>
            )}
            {still && <p className="muted">Arquivo: {still.name}</p>}
            {(camera.status === 'error' || camera.status === 'suspended') && (
              <p className="error" role="alert">
                {camera.message}
              </p>
            )}
            {inferenceError && (
              <p className="error" role="alert">
                {inferenceError}
              </p>
            )}
            {captureError && (
              <p className="error" role="alert">
                {captureError}
              </p>
            )}
            <p className="muted">
              Capturar e registrar grava a foto sem as marcações e abre o registro com a localização
              do aparelho. Analisar imagem é só uma prévia: a imagem não é enviada.
            </p>
          </section>

          <section className="panel" aria-label="Detecções do quadro analisado" aria-live="polite">
            <h2>O que a detecção encontrou</h2>
            {!analyzed ? (
              <p className="muted">As detecções aparecem aqui quando a análise começar.</p>
            ) : analyzed.detections.length === 0 ? (
              <p>Nenhum buraco ou trinca apareceu nesta imagem.</p>
            ) : (
              <>
                <ol className="live-detections">
                  {analyzed.detections.slice(0, limit).map((detection, index) => (
                    <li key={index}>
                      <i
                        style={{ background: classColor(detection.classIndex) }}
                        aria-hidden="true"
                      />
                      <span>
                        <strong>{labelFor(detection.className)}</strong>
                      </span>
                    </li>
                  ))}
                </ol>
                {analyzed.detections.length > limit && (
                  <p className="muted">
                    Exibindo {limit} de {analyzed.detections.length} detecções.
                  </p>
                )}
              </>
            )}
            <p className="muted">
              {manifest && manifest.scientific_status !== 'APPROVED'
                ? 'Detecção automática em teste: o resultado não confirma o problema. A equipe revisa cada relato.'
                : 'O resultado automático não confirma o problema: a equipe revisa cada relato.'}
            </p>
          </section>

          <details className="panel live-technical">
            <summary>Detalhes técnicos</summary>
            <DetectionReadout
              metrics={metrics}
              live={live}
              declaredFps={live ? camera.declaredFps : null}
              resolution={live ? `${camera.width}×${camera.height}` : null}
              frameId={analyzed?.frameId ?? null}
            />
            {analyzed && analyzed.detections.length > 0 && (
              <dl className="live-model" aria-label="Confiança do modelo">
                {analyzed.detections.slice(0, limit).map((detection, index) => (
                  <div key={index}>
                    <dt>{labelFor(detection.className)}</dt>
                    <dd title="Confiança do modelo, de 0 a 1; não é gravidade nem confirmação">
                      confiança {scoreFormat.format(detection.score)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
            {manifest && (
              <dl className="live-model">
                <div>
                  <dt>Versão</dt>
                  <dd>{manifest.model_version}</dd>
                </div>
                <div>
                  <dt>Status científico</dt>
                  <dd>{statusLabels[manifest.scientific_status]}</dd>
                </div>
                <div>
                  <dt>Classes do artefato</dt>
                  <dd>{manifest.class_names.map(labelFor).join(', ')}</dd>
                </div>
                <div>
                  <dt>Perfil de inferência</dt>
                  <dd title={manifest.inference_profile?.sha256}>
                    {manifest.inference_profile
                      ? manifest.inference_profile.sha256.slice(0, 12)
                      : 'não versionado'}
                  </dd>
                </div>
                <div>
                  <dt>Execução</dt>
                  <dd>
                    {model.status === 'ready'
                      ? model.provider === 'webgpu'
                        ? 'WebGPU'
                        : 'WASM (1 thread)'
                      : '—'}
                  </dd>
                </div>
              </dl>
            )}
            {model.status === 'ready' && model.fallbackReason && (
              <p className="muted">{model.fallbackReason}</p>
            )}
            <label className="live-toggle">
              <input
                type="checkbox"
                checked={stabilize}
                onChange={(event) => setStabilize(event.target.checked)}
              />
              Tracejar detecções que ainda não se repetiram em quadros seguidos
            </label>
          </details>
        </div>
      </div>
    </>
  );
}

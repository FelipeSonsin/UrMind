import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Camera as CameraIcon,
  CircleAlert,
  CircleCheck,
  ImageUp,
  LoaderCircle,
  LocateFixed,
  MapPin,
  Send,
} from 'lucide-react';
import { useAppState } from '../../app/AppState';
import { AddressSearch } from '../../components/AddressSearch';
import { Camera } from '../../components/Camera';
import { Photo } from '../../components/Photo';
import { PhotoCheck } from '../../components/PhotoCheck';
import { PageHeader } from '../../components/ui';
import { coordinateSchema } from '../../domain/contracts';
import {
  defaultPhotoPolicy,
  drafts,
  fitCameraPhoto,
  measurePhoto,
  validatePhoto,
  type CaptureDraft,
  type PhotoMeasure,
} from '../../services/drafts';
import {
  currentFix,
  fixMatchesPhoto,
  stopWarmUp,
  warmUp,
  warmUpIfAllowed,
} from '../../services/deviceLocation';
import { publicApi } from '../../services/publicApi';
import { MAP_DESIGN } from '../../mapDesign';
import { gps } from 'exifr';
const UrbanMap = lazy(() => import('../../components/UrbanMap'));

const NO_EVENTS: [] = [];

/**
 * Onde o mapa abre quando a foto não tem localização: a última região usada neste
 * aparelho (arredondada, ~1 km) ou a área piloto — nunca o Brasil inteiro.
 */
const LAST_AREA_KEY = 'urmind.lastArea';
function lastArea(): { latitude: number; longitude: number } | null {
  try {
    const value = JSON.parse(localStorage.getItem(LAST_AREA_KEY) ?? 'null');
    const parsed = coordinateSchema.pick({ latitude: true, longitude: true }).safeParse(value);
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}
function rememberArea(point: { latitude: number; longitude: number }) {
  try {
    localStorage.setItem(
      LAST_AREA_KEY,
      JSON.stringify({
        latitude: Math.round(point.latitude * 100) / 100,
        longitude: Math.round(point.longitude * 100) / 100,
      }),
    );
  } catch {
    // Sem armazenamento local, o mapa abre na área piloto.
  }
}

/** Some Android cameras hand back a file without extension; name it by its type. */
function cameraFile(file: File): File {
  if (/\.(jpe?g|png|webp)$/i.test(file.name)) return file;
  const extension = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' }[file.type];
  if (!extension) return file;
  return new File([file], `foto-${Date.now()}.${extension}`, {
    type: file.type,
    lastModified: file.lastModified,
  });
}

function newDraft(): CaptureDraft {
  return {
    id: crypto.randomUUID(),
    photo: new Blob(),
    filename: '',
    source: 'exif_upload',
    captured_at: null,
    created_at: new Date().toISOString(),
    coordinate: null,
    source_location: 'unknown',
    location_timestamp: null,
    heading_deg: null,
    speed_mps: null,
    note: '',
    status: 'local_draft',
  };
}

/** A photo taken now (camera or live capture) can still get the device position. */
function takenNow(draft: CaptureDraft, now = Date.now()) {
  // Quadro da câmera do robô: o aparelho que registra não é o que fotografou.
  if (draft.camera_origin === 'robot_remote') return false;
  return draft.source === 'pwa_photo' && fixMatchesPhoto(now, draft.captured_at);
}

export function CapturePage({
  initial,
  onSaved,
}: {
  initial?: CaptureDraft;
  onSaved: (draft: CaptureDraft) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState<CaptureDraft>(() => initial || newDraft());
  const [camera, setCamera] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // Conferir a foto escolhida não é enviar: o botão diz o que está acontecendo.
  const [checking, setChecking] = useState(false);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState('');
  // "Confirmar local" antes de marcar o ponto: o botão diz o que falta, sem ficar cinza.
  const [askPick, setAskPick] = useState(false);
  const [pickedLocation, setPickedLocation] = useState<{
    latitude: number;
    longitude: number;
  } | null>(null);
  const [showMap, setShowMap] = useState(false);
  const [addressPoint, setAddressPoint] = useState<{
    latitude: number;
    longitude: number;
  } | null>(null);
  const [focusArea] = useState(() => lastArea() ?? MAP_DESIGN.framing.pilotArea);
  // Each photo (and each manual point) starts a new selection: a GPS answer that
  // arrives for an earlier one is discarded instead of landing on the current photo.
  const selectionVersion = useRef(0);
  const capturedAt = useRef<string | null>(initial?.captured_at ?? null);
  const [photoPolicy, setPhotoPolicy] = useState(defaultPhotoPolicy);
  // O que a checagem técnica mediu na foto atual (mostrado sob a foto).
  const [photoMeasure, setPhotoMeasure] = useState<PhotoMeasure | null>(null);
  const [privacy, setPrivacy] = useState<{ version: string; text: string } | null>(null);
  const [acceptedVersion, setAcceptedVersion] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void publicApi
      .privacyNotice(controller.signal)
      .then((notice) => {
        if (!controller.signal.aborted) setPrivacy(notice);
      })
      .catch(() => {
        // No network: a draft can remain local, but upload requires the current notice.
      });
    void publicApi
      .photoPolicy(controller.signal)
      .then((policy) => {
        if (!controller.signal.aborted) setPhotoPolicy(policy);
      })
      .catch(() => {
        /* Offline drafts use conservative defaults; server always rechecks. */
      });
    return () => controller.abort();
  }, []);
  // With permission already granted, GPS starts settling while the person
  // frames the photo; otherwise it starts on the "Tirar foto" tap.
  useEffect(() => {
    void warmUpIfAllowed();
    return stopWarmUp;
  }, []);
  // A frame captured in live detection a moment ago gets the device position
  // right away; an older draft is located on the map instead.
  // Rascunho reaberto: mede a foto guardada para mostrar a checagem técnica.
  useEffect(() => {
    if (!initial?.photo.size) return;
    let current = true;
    const file = new File([initial.photo], initial.filename || 'foto.jpg', {
      type: initial.photo.type,
    });
    measurePhoto(file)
      .then((measure) => {
        if (current) setPhotoMeasure(measure);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
    // Runs once for the draft this page was opened with.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (initial && !initial.coordinate && takenNow(initial)) void locate();
    else if (initial?.photo.size && !initial.coordinate) setShowMap(true);
    // Runs once for the draft this page was opened with.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function select(picked: File, fromCamera = false) {
    const version = ++selectionVersion.current;
    setChecking(true);
    setError('');
    try {
      const file = fromCamera ? await fitCameraPhoto(cameraFile(picked)) : picked;
      const measure = await validatePhoto(file, photoPolicy);
      if (version !== selectionVersion.current) return;
      // Browser EXIF is a preview only. The backend re-reads the original bytes
      // and decides the persisted location/timestamp independently.
      let exifCoordinate: CaptureDraft['coordinate'] = null;
      if (!fromCamera) {
        try {
          const position = await gps(file);
          const candidate = coordinateSchema.safeParse({
            latitude: position?.latitude,
            longitude: position?.longitude,
            // EXIF has no accuracy of its own; none is invented here.
            accuracy_m: null,
          });
          if (
            candidate.success &&
            !(Math.abs(candidate.data.latitude) < 1e-9 && Math.abs(candidate.data.longitude) < 1e-9)
          )
            exifCoordinate = candidate.data;
        } catch {
          // No readable EXIF: the place is marked on the map.
        }
      }
      if (version !== selectionVersion.current) return;
      const taken = fromCamera ? new Date().toISOString() : null;
      capturedAt.current = taken;
      setPhotoMeasure(measure);
      setDraft((value) => ({
        ...value,
        photo: file,
        filename: file.name,
        source: fromCamera ? 'pwa_photo' : 'exif_upload',
        camera_origin: undefined,
        captured_at: taken,
        coordinate: exifCoordinate,
        source_location: exifCoordinate ? 'exif' : 'unknown',
        location_timestamp: null,
        heading_deg: null,
        speed_mps: null,
      }));
      setPickedLocation(null);
      // Gallery photo: its own GPS or the map. Never the device's current position.
      setShowMap(!fromCamera && !exifCoordinate);
      setCamera(false);
      setLocationError('');
      setLocating(false);
      // A photo taken now is located by the phone itself, no map or typing.
      if (fromCamera) void locate();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setChecking(false);
    }
  }

  async function locate() {
    const version = selectionVersion.current;
    setLocating(true);
    setLocationError('');
    try {
      const position = await currentFix();
      if (version !== selectionVersion.current) return;
      if (!fixMatchesPhoto(position.timestamp, capturedAt.current)) {
        setLocationError(
          'A foto foi tirada há mais de 2 minutos e você pode ter se deslocado. Marque no mapa onde ela foi tirada.',
        );
        setShowMap(true);
        return;
      }
      const result = coordinateSchema.safeParse({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy_m: position.coords.accuracy,
      });
      if (!result.success) {
        setLocationError('O aparelho devolveu uma posição inválida. Marque o local no mapa.');
        setShowMap(true);
        return;
      }
      setDraft((value) => ({
        ...value,
        coordinate: result.data,
        source_location: 'gps_device',
        location_timestamp: new Date(position.timestamp).toISOString(),
        heading_deg: position.coords.heading,
        speed_mps: position.coords.speed,
      }));
      setShowMap(false);
    } catch (reason) {
      if (version !== selectionVersion.current) return;
      setLocationError(
        reason instanceof Error ? reason.message : 'Não foi possível obter a localização.',
      );
      setShowMap(true);
    } finally {
      if (version === selectionVersion.current) setLocating(false);
    }
  }

  function openMap() {
    setPickedLocation(null);
    setAddressPoint(null);
    setShowMap(true);
  }

  function confirmMapLocation() {
    if (!pickedLocation) return;
    setAskPick(false);
    // A confirmed point wins over a GPS answer still on its way.
    selectionVersion.current += 1;
    setLocating(false);
    setLocationError('');
    rememberArea(pickedLocation);
    setDraft((value) => ({
      ...value,
      coordinate: { ...pickedLocation, accuracy_m: null },
      source_location: 'manual',
      location_timestamp: null,
      heading_deg: null,
      speed_mps: null,
    }));
    setShowMap(false);
  }

  const app = useAppState();
  const appRef = useRef(app);
  appRef.current = app;
  const latestDraft = useRef(draft);
  latestDraft.current = draft;
  const submitted = useRef(false);
  // Sair sem enviar (outra aba, voltar): a foto vai para os rascunhos e não se perde.
  // Rascunho reaberto e não alterado não é salvo de novo.
  useEffect(
    () => () => {
      const current = latestDraft.current;
      if (submitted.current || !current.photo.size || current === initial) return;
      void drafts.save(current).then(() => {
        appRef.current.setNotice('A foto que você não enviou ficou guardada em Rascunhos.');
        void appRef.current.drafts.reload();
      });
    },
    // Só ao sair da página.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  async function save(event: FormEvent) {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (!draft.photo.size) throw new Error('Tire ou escolha uma foto antes de enviar.');
      if (!privacy || acceptedVersion !== privacy.version) {
        await drafts.save(draft);
        throw new Error(
          'Leia e aceite o aviso de privacidade antes de enviar. Rascunho preservado.',
        );
      }
      const saved: CaptureDraft = {
        ...draft,
        privacy_version: privacy.version,
        source_location: draft.coordinate ? draft.source_location : 'unknown',
      };
      if (saved.coordinate) rememberArea(saved.coordinate);
      await drafts.save(saved);
      submitted.current = true;
      await onSaved(saved);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Não foi possível salvar. Verifique o armazenamento do navegador.',
      );
    } finally {
      setBusy(false);
    }
  }

  const hasPhoto = draft.photo.size > 0;
  const located = draft.coordinate != null && draft.source_location !== 'unknown';
  const canRetryGps = !locating && takenNow(draft);

  return (
    <>
      <PageHeader
        title={initial ? 'Continuar relato' : 'Registrar relato'}
        description="Mande a foto do problema. A equipe confere antes de publicar."
      />
      <form onSubmit={save} className="capture-grid">
        <section className="panel" aria-labelledby="capture-photo">
          <h2 id="capture-photo">
            <span className="step" data-done={hasPhoto}>
              1
            </span>{' '}
            Foto
          </h2>
          {/* Com a câmera aberta, ela ocupa o lugar da foto e dos botões até fotografar. */}
          {camera ? (
            <Camera
              onCapture={(file) => void select(file, true)}
              onClose={() => setCamera(false)}
            />
          ) : (
            <div className="upload-area">
              {hasPhoto ? (
                <Photo blob={draft.photo} alt="Evidência selecionada" />
              ) : (
                <>
                  <ImageUp size={36} strokeWidth={1.4} />
                  <h3>Mostre o problema de perto</h3>
                </>
              )}
            </div>
          )}
          {!camera && hasPhoto && photoMeasure && (
            <PhotoCheck measure={photoMeasure} policy={photoPolicy} />
          )}
          <div className="actions photo-actions" hidden={camera}>
            {/* The phone's own camera app: full resolution, and the tap also
                starts GPS so the position is ready when the photo returns.
                On a computer the same name opens the webcam (below). */}
            <label className="button phone-camera">
              <CameraIcon size={17} /> Tirar foto
              <input
                aria-label="Tirar foto"
                className="file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                capture="environment"
                disabled={busy || checking}
                onClick={() => warmUp()}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void select(file, true);
                  event.target.value = '';
                }}
              />
            </label>
            <button
              type="button"
              className="desktop-camera"
              disabled={busy || checking}
              onClick={() => {
                warmUp();
                setCamera(true);
              }}
            >
              <CameraIcon size={17} /> Tirar foto
            </button>
            <label className="button secondary">
              Escolher foto
              <input
                aria-label="Escolher foto"
                className="file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                disabled={busy || checking}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void select(file);
                  event.target.value = '';
                }}
              />
            </label>
          </div>
        </section>
        <section className="panel capture-details">
          <h2>
            <span className="step" data-done={located}>
              2
            </span>{' '}
            Localização
          </h2>
          <div
            className={`location-status${located ? ' is-located' : ''}${locationError ? ' is-error' : ''}`}
            role="status"
            aria-live="polite"
          >
            {!hasPhoto ? (
              <p className="muted">
                O local vem do GPS do aparelho ou da própria foto. Se faltar, você marca no mapa.
              </p>
            ) : locating ? (
              <p>
                <LoaderCircle size={18} className="spin" aria-hidden="true" />{' '}
                <strong>Obtendo localização…</strong>
                <small>O envio espera a localização ou o ponto marcado no mapa.</small>
              </p>
            ) : located && draft.source_location === 'gps_device' ? (
              <p>
                <CircleCheck size={18} aria-hidden="true" /> <strong>Localização obtida</strong>
                <small>
                  {draft.coordinate?.accuracy_m != null
                    ? `Precisão aproximada: ${Math.round(draft.coordinate.accuracy_m)} m`
                    : 'O aparelho não informou a precisão.'}
                </small>
              </p>
            ) : located && draft.source_location === 'exif' ? (
              <p>
                <CircleCheck size={18} aria-hidden="true" />{' '}
                <strong>Localização encontrada na foto</strong>
                <small>Se o ponto não estiver certo, corrija no mapa.</small>
              </p>
            ) : located ? (
              <p>
                <CircleCheck size={18} aria-hidden="true" /> <strong>Local marcado no mapa</strong>
                <small>Você pode corrigir o ponto antes de enviar.</small>
              </p>
            ) : locationError ? (
              <p>
                <CircleAlert size={18} aria-hidden="true" />{' '}
                <strong>Localização indisponível</strong>
                <small>{locationError}</small>
              </p>
            ) : (
              <p>
                <MapPin size={18} aria-hidden="true" /> <strong>Falta o local da foto</strong>
                {/* Com o mapa aberto, o título dele já diz o que fazer; aqui só o porquê. */}
                <small>
                  {showMap
                    ? 'A foto não trouxe a localização.'
                    : 'Marque no mapa onde a foto foi tirada.'}
                </small>
              </p>
            )}
            {hasPhoto && !showMap && (
              <div className="actions">
                {!located && canRetryGps && (
                  <button type="button" className="secondary" onClick={() => void locate()}>
                    <LocateFixed size={16} /> Tentar de novo
                  </button>
                )}
                {!locating && (
                  <button type="button" className="secondary" onClick={openMap}>
                    <MapPin size={16} /> {located ? 'Corrigir no mapa' : 'Marcar no mapa'}
                  </button>
                )}
              </div>
            )}
            {locationError && canRetryGps && showMap && (
              <button type="button" className="secondary" onClick={() => void locate()}>
                <LocateFixed size={16} /> Tentar de novo
              </button>
            )}
          </div>
          {hasPhoto && showMap && (
            <div className="location-picker">
              <h3>Selecione no mapa onde esta foto foi tirada</h3>
              <AddressSearch
                onChoose={(point) => {
                  setAddressPoint(point);
                  setPickedLocation(point);
                }}
              />
              <Suspense fallback={<p role="status">Carregando mapa…</p>}>
                <UrbanMap
                  events={NO_EVENTS}
                  initialCenter={draft.coordinate ?? focusArea}
                  initialZoom={draft.coordinate ? undefined : MAP_DESIGN.framing.areaZoom}
                  pickedPoint={addressPoint}
                  onPickLocation={(latitude, longitude) =>
                    setPickedLocation({ latitude, longitude })
                  }
                />
              </Suspense>
              {askPick && !pickedLocation && (
                <p className="field-error" role="alert">
                  Antes, arraste o mapa até o alvo ficar sobre o local do problema ou escolha um
                  endereço.
                </p>
              )}
              <div className="actions">
                <button
                  type="button"
                  onClick={() => (pickedLocation ? confirmMapLocation() : setAskPick(true))}
                >
                  <MapPin size={16} /> Confirmar local
                </button>
                {located && (
                  <button type="button" className="secondary" onClick={() => setShowMap(false)}>
                    Cancelar
                  </button>
                )}
              </div>
            </div>
          )}
          <h2>
            <span className="step" data-done={draft.note.trim().length > 0}>
              3
            </span>{' '}
            Descrição
          </h2>
          <label>
            Descreva o que você observou (opcional)
            <textarea
              rows={3}
              maxLength={500}
              value={draft.note}
              placeholder="Ex.: buraco grande perto da faixa de pedestres…"
              onChange={(event) => setDraft((value) => ({ ...value, note: event.target.value }))}
            />
          </label>
          {draft.note.length > 0 && (
            <p className="muted char-count">{draft.note.length}/500 caracteres</p>
          )}
          <h2>
            <span className="step">4</span> Enviar
          </h2>
          <section aria-label="Privacidade do relato" className="consent">
            {privacy ? (
              <>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={acceptedVersion === privacy.version}
                    onChange={(event) =>
                      setAcceptedVersion(event.target.checked ? privacy.version : null)
                    }
                  />
                  Li e aceito o armazenamento da foto e da localização
                </label>
                <details>
                  <summary>Saiba mais sobre privacidade</summary>
                  <p>{privacy.text}</p>
                </details>
              </>
            ) : (
              <p role="status" className="muted">
                Aviso de privacidade indisponível sem conexão. O relato fica guardado neste aparelho
                até você enviar.
              </p>
            )}
          </section>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy || checking || locating}>
            <Send size={17} />{' '}
            {busy ? 'Enviando…' : checking ? 'Conferindo a foto…' : 'Enviar relato'}
          </button>
          {hasPhoto && !located && !locating && (
            <p className="muted">Sem localização, você poderá marcar o ponto depois do envio.</p>
          )}
        </section>
      </form>
    </>
  );
}

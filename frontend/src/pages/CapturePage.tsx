import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Camera as CameraIcon,
  CircleAlert,
  CircleCheck,
  ImageUp,
  LoaderCircle,
  LocateFixed,
  MapPin,
  Search,
  Send,
} from 'lucide-react';
import { Camera } from '../components/Camera';
import { Photo } from '../components/Photo';
import { coordinateSchema } from '../domain/contracts';
import {
  defaultPhotoPolicy,
  drafts,
  fitCameraPhoto,
  validatePhoto,
  type CaptureDraft,
} from '../services/drafts';
import {
  currentFix,
  fixMatchesPhoto,
  stopWarmUp,
  warmUp,
  warmUpIfAllowed,
} from '../services/deviceLocation';
import { publicApi, type AddressResult } from '../services/publicApi';
import { MAP_DESIGN } from '../mapDesign';
import { gps } from 'exifr';
const UrbanMap = lazy(() => import('../components/UrbanMap'));

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
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState('');
  const [pickedLocation, setPickedLocation] = useState<{
    latitude: number;
    longitude: number;
  } | null>(null);
  const [showMap, setShowMap] = useState(false);
  const [addressQuery, setAddressQuery] = useState('');
  const [addressResults, setAddressResults] = useState<AddressResult[] | null>(null);
  const [addressAttribution, setAddressAttribution] = useState('');
  const [addressError, setAddressError] = useState('');
  const [searching, setSearching] = useState(false);
  const [addressPoint, setAddressPoint] = useState<{
    latitude: number;
    longitude: number;
  } | null>(null);
  const [chosenAddress, setChosenAddress] = useState<number | null>(null);
  const searchController = useRef<AbortController | null>(null);
  useEffect(() => () => searchController.current?.abort(), []);
  const [focusArea] = useState(() => lastArea() ?? MAP_DESIGN.framing.pilotArea);
  // Each photo (and each manual point) starts a new selection: a GPS answer that
  // arrives for an earlier one is discarded instead of landing on the current photo.
  const selectionVersion = useRef(0);
  const capturedAt = useRef<string | null>(initial?.captured_at ?? null);
  const [photoPolicy, setPhotoPolicy] = useState(defaultPhotoPolicy);
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
  useEffect(() => {
    if (initial && !initial.coordinate && takenNow(initial)) void locate();
    else if (initial?.photo.size && !initial.coordinate) setShowMap(true);
    // Runs once for the draft this page was opened with.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function select(picked: File, fromCamera = false) {
    const version = ++selectionVersion.current;
    setBusy(true);
    setError('');
    try {
      const file = fromCamera ? await fitCameraPhoto(cameraFile(picked)) : picked;
      await validatePhoto(file, photoPolicy);
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
      setBusy(false);
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
    setChosenAddress(null);
    setShowMap(true);
  }

  /** Busca só quando a pessoa pede (botão ou Enter): nada de consulta a cada tecla. */
  async function searchAddress() {
    const query = addressQuery.trim();
    if (query.length < 3 || searching) return;
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    setSearching(true);
    setAddressError('');
    try {
      const found = await publicApi.searchAddress(query, controller.signal);
      if (controller.signal.aborted) return;
      setAddressResults(found.results);
      setAddressAttribution(found.attribution);
      setChosenAddress(null);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setAddressResults(null);
        setAddressError(
          reason instanceof Error ? reason.message : 'Busca de endereço indisponível.',
        );
      }
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  }

  function chooseAddress(result: AddressResult, index: number) {
    const point = { latitude: result.latitude, longitude: result.longitude };
    setChosenAddress(index);
    setAddressPoint(point);
    setPickedLocation(point);
  }

  function confirmMapLocation() {
    if (!pickedLocation) return;
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
      <div className="page-heading">
        <div>
          <h1>{initial ? 'Continuar relato' : 'Registrar evidência'}</h1>
          <p>Envie uma foto do problema. A localização vem do aparelho ou da própria foto.</p>
        </div>
      </div>
      <form onSubmit={save} className="capture-grid">
        <section className="panel" aria-labelledby="capture-photo">
          <h2 id="capture-photo">
            <span className="step" data-done={hasPhoto}>
              1
            </span>{' '}
            Foto
          </h2>
          <div className="upload-area">
            {hasPhoto ? (
              <Photo blob={draft.photo} alt="Evidência selecionada" />
            ) : (
              <>
                <ImageUp size={36} strokeWidth={1.4} />
                <h3>Mostre o problema de perto</h3>
                <p>Tire na hora ou escolha da galeria.</p>
                <small>JPEG, PNG ou WebP, até 10 MB</small>
              </>
            )}
          </div>
          <div className="actions">
            {/* The phone's own camera app: full resolution, and the tap also
                starts GPS so the position is ready when the photo returns. */}
            <label className="button">
              <CameraIcon size={17} /> Tirar foto
              <input
                aria-label="Tirar foto"
                className="file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                capture="environment"
                disabled={busy}
                onClick={() => warmUp()}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void select(file, true);
                  event.target.value = '';
                }}
              />
            </label>
            <label className="button secondary">
              Escolher foto
              <input
                aria-label="Escolher foto"
                className="file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void select(file);
                  event.target.value = '';
                }}
              />
            </label>
            <button
              type="button"
              className="secondary desktop-camera"
              disabled={busy}
              onClick={() => {
                warmUp();
                setCamera(true);
              }}
            >
              <CameraIcon size={17} /> Abrir câmera
            </button>
          </div>
          {camera && (
            <Camera
              onCapture={(file) => void select(file, true)}
              onClose={() => setCamera(false)}
            />
          )}
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
                Foto tirada agora usa o GPS do aparelho. Foto da galeria usa a localização gravada
                nela; se não houver, você marca no mapa.
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
                    : 'Precisão não informada pelo aparelho'}
                </small>
              </p>
            ) : located && draft.source_location === 'exif' ? (
              <p>
                <CircleCheck size={18} aria-hidden="true" />{' '}
                <strong>Localização encontrada na foto</strong>
                <small>A posição gravada na foto é conferida no envio.</small>
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
                <MapPin size={18} aria-hidden="true" />{' '}
                <strong>Precisamos que você confirme onde a foto foi tirada</strong>
                <small>Se a foto não tiver localização, marque onde ela foi tirada.</small>
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
              <div className="address-search" role="search">
                <label>
                  Buscar endereço ou CEP
                  <input
                    type="search"
                    value={addressQuery}
                    maxLength={200}
                    autoComplete="street-address"
                    placeholder="Ex.: Rua Galvão Bueno, 100, São Paulo"
                    onChange={(event) => setAddressQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        // Enter busca o endereço; nunca envia o relato.
                        event.preventDefault();
                        void searchAddress();
                      }
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="secondary"
                  disabled={searching || addressQuery.trim().length < 3}
                  onClick={() => void searchAddress()}
                >
                  <Search size={16} aria-hidden="true" /> {searching ? 'Buscando…' : 'Buscar'}
                </button>
              </div>
              {addressError && (
                <p className="field-error" role="alert">
                  {addressError}
                </p>
              )}
              {addressResults &&
                (addressResults.length ? (
                  <ul className="address-results" aria-label="Endereços encontrados">
                    {addressResults.map((result, index) => (
                      <li key={`${result.latitude},${result.longitude},${index}`}>
                        <button
                          type="button"
                          aria-pressed={chosenAddress === index}
                          onClick={() => chooseAddress(result, index)}
                        >
                          {result.label}
                          {result.detail && <small>{result.detail}</small>}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="address-hint" role="status">
                    Nenhum endereço encontrado. Tente com a rua e a cidade, ou marque no mapa.
                  </p>
                ))}
              <p className="address-hint">
                {chosenAddress != null
                  ? 'Confira o marcador e toque no mapa para ajustar ao local exato.'
                  : 'Ou toque no mapa onde a foto foi tirada.'}
              </p>
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
              {addressAttribution && (
                <p className="map-caption">Busca de endereço: {addressAttribution}</p>
              )}
              <div className="actions">
                <button type="button" disabled={!pickedLocation} onClick={confirmMapLocation}>
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
              placeholder="Ex.: buraco grande perto da faixa de pedestres"
              onChange={(event) => setDraft((value) => ({ ...value, note: event.target.value }))}
            />
          </label>
          <p className="muted char-count" aria-live="polite">
            {draft.note.length}/500 caracteres
          </p>
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
          <button type="submit" disabled={busy || locating}>
            <Send size={17} /> {busy ? 'Enviando…' : 'Enviar relato'}
          </button>
          {hasPhoto && !located && !locating && (
            <p className="muted">Sem localização, você poderá marcar o ponto depois do envio.</p>
          )}
        </section>
      </form>
    </>
  );
}

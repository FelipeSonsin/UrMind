import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react';
import { Camera as CameraIcon, Upload, LocateFixed, Save } from 'lucide-react';
import { Camera } from '../components/Camera';
import { Photo } from '../components/Photo';
import { parseCoordinate, coordinateSchema } from '../domain/contracts';
import { defaultPhotoPolicy, drafts, validatePhoto, type CaptureDraft } from '../services/drafts';
import { publicApi } from '../services/publicApi';
import { gps } from 'exifr';
const UrbanMap = lazy(() => import('../components/UrbanMap'));

const NO_EVENTS: [] = [];

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
export function CapturePage({
  initial,
  onSaved,
}: {
  initial?: CaptureDraft;
  onSaved: (draft: CaptureDraft) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState<CaptureDraft>(() => initial || newDraft());
  const [latitude, setLatitude] = useState(initial?.coordinate?.latitude.toString() || '');
  const [longitude, setLongitude] = useState(initial?.coordinate?.longitude.toString() || '');
  const [camera, setCamera] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [locating, setLocating] = useState(false);
  const [pickedLocation, setPickedLocation] = useState<{
    latitude: number;
    longitude: number;
  } | null>(null);
  const [showMap, setShowMap] = useState(false);
  const selectionVersion = useRef(0);
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
  const candidateCenter = coordinateSchema.safeParse({
    latitude: Number(latitude),
    longitude: Number(longitude),
    accuracy_m: null,
  });
  async function select(file: File, fromCamera = false) {
    const version = ++selectionVersion.current;
    setBusy(true);
    setError('');
    try {
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
            accuracy_m: null,
          });
          if (
            candidate.success &&
            !(Math.abs(candidate.data.latitude) < 1e-9 && Math.abs(candidate.data.longitude) < 1e-9)
          )
            exifCoordinate = candidate.data;
        } catch {
          // No readable EXIF: require a point explicitly confirmed on the map.
        }
      }
      if (version !== selectionVersion.current) return;
      setDraft((value) => ({
        ...value,
        photo: file,
        filename: file.name,
        source: fromCamera ? 'pwa_photo' : 'exif_upload',
        captured_at: fromCamera ? new Date().toISOString() : null,
        coordinate: exifCoordinate,
        source_location: exifCoordinate ? 'exif' : 'unknown',
        location_timestamp: null,
        heading_deg: null,
        speed_mps: null,
      }));
      setLatitude(exifCoordinate ? String(exifCoordinate.latitude) : '');
      setLongitude(exifCoordinate ? String(exifCoordinate.longitude) : '');
      setPickedLocation(null);
      setShowMap(!fromCamera && !exifCoordinate);
      setCamera(false);
      if (fromCamera) locate();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function locate() {
    const version = selectionVersion.current;
    if (!navigator.geolocation) {
      setError('Geolocalização indisponível neste navegador.');
      return;
    }
    setLocating(true);
    setError('');
    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (version !== selectionVersion.current) return;
        const result = coordinateSchema.safeParse({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy_m: position.coords.accuracy,
        });
        if (!result.success) {
          setError('O dispositivo retornou uma localização inválida.');
          setLocating(false);
          return;
        }
        setLatitude(String(result.data.latitude));
        setLongitude(String(result.data.longitude));
        setDraft((value) => ({
          ...value,
          coordinate: result.data,
          source_location: 'gps_device',
          location_timestamp: new Date(position.timestamp).toISOString(),
          heading_deg: position.coords.heading,
          speed_mps: position.coords.speed,
        }));
        setLocating(false);
      },
      () => {
        if (version !== selectionVersion.current) return;
        setError(
          'Não foi possível obter a localização. Verifique a permissão ou informe as coordenadas.',
        );
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  }
  function manual() {
    // A confirmed/edited manual point wins over a pending Geolocation callback.
    selectionVersion.current += 1;
    setLocating(false);
    setDraft((value) => ({
      ...value,
      source_location: 'manual',
      coordinate: null,
      location_timestamp: null,
      heading_deg: null,
      speed_mps: null,
    }));
  }
  function confirmMapLocation() {
    if (!pickedLocation) return;
    selectionVersion.current += 1;
    setLocating(false);
    setLatitude(String(pickedLocation.latitude));
    setLongitude(String(pickedLocation.longitude));
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
      if (!draft.photo.size) throw new Error('Selecione ou tire uma foto antes de salvar.');
      if (!privacy || acceptedVersion !== privacy.version) {
        await drafts.save(draft);
        throw new Error(
          'Leia e aceite o aviso de privacidade antes de enviar. Rascunho preservado.',
        );
      }
      const coordinate =
        latitude.trim() || longitude.trim() ? parseCoordinate(latitude, longitude) : null;
      if (
        coordinate &&
        draft.source_location !== 'gps_device' &&
        (!draft.coordinate ||
          draft.coordinate.latitude !== coordinate.latitude ||
          draft.coordinate.longitude !== coordinate.longitude)
      )
        throw new Error('Selecione e confirme a localização no mapa antes do envio.');
      const saved: CaptureDraft = {
        ...draft,
        privacy_version: privacy.version,
        coordinate:
          coordinate && draft.source_location === 'gps_device' ? draft.coordinate : coordinate,
        source_location: coordinate ? draft.source_location : 'unknown',
      };
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
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">EVIDÊNCIAS / NOVO REGISTRO</p>
          <h1>{initial ? 'Editar rascunho' : 'Registrar uma evidência'}</h1>
          <p>Comece pela foto. Cada informação mantém sua origem.</p>
        </div>
        <span className="badge">Armazenamento local</span>
      </div>
      <form onSubmit={save} className="capture-grid">
        <section className="panel">
          <h2>
            <span className="step">01</span> Fotografia
          </h2>
          <div className="upload-area">
            {draft.photo.size ? (
              <Photo blob={draft.photo} alt="Evidência selecionada" />
            ) : (
              <>
                <Upload size={36} strokeWidth={1.4} />
                <h3>O primeiro olhar sobre a cidade</h3>
                <p>Importe uma foto ou utilize a câmera.</p>
                <small>JPEG, PNG ou WebP · até 10 MB</small>
              </>
            )}
          </div>
          <div className="actions">
            <label className="button secondary">
              Escolher foto
              <input
                aria-label="Escolher foto"
                className="file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                disabled={busy || locating}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void select(file);
                  event.target.value = '';
                }}
              />
            </label>
            <button
              type="button"
              className="secondary"
              disabled={busy || locating}
              onClick={() => setCamera(true)}
            >
              <CameraIcon size={17} /> Abrir câmera
            </button>
          </div>
          {draft.filename && <p className="muted filename">{draft.filename}</p>}
          {camera && (
            <Camera
              onCapture={(file) => void select(file, true)}
              onClose={() => setCamera(false)}
            />
          )}
        </section>
        <section className="panel">
          <h2>
            <span className="step">02</span> Localização e contexto
          </h2>
          <p>
            Informe o local em que a foto foi tirada. Sem localização, o rascunho fica pendente.
          </p>
          {draft.source === 'exif_upload' && (
            <p className="notice">
              {draft.source_location === 'exif'
                ? 'GPS EXIF encontrado. O servidor confirmará os metadados da foto original.'
                : 'Sem GPS EXIF válido. Selecione e confirme no mapa onde a foto foi tirada.'}
            </p>
          )}
          <div className="field-row">
            <label>
              Latitude
              <input
                inputMode="decimal"
                placeholder="−90 a 90"
                value={latitude}
                disabled={locating}
                onChange={(event) => {
                  setLatitude(event.target.value);
                  manual();
                }}
              />
            </label>
            <label>
              Longitude
              <input
                inputMode="decimal"
                placeholder="−180 a 180"
                value={longitude}
                disabled={locating}
                onChange={(event) => {
                  setLongitude(event.target.value);
                  manual();
                }}
              />
            </label>
          </div>
          <button type="button" className="secondary" onClick={() => setShowMap((value) => !value)}>
            {showMap ? 'Fechar mapa' : 'Selecionar localização no mapa'}
          </button>
          {showMap && (
            <div>
              <p>Toque no local onde a foto foi tirada e confirme o marcador.</p>
              <Suspense fallback={<p>Carregando mapa…</p>}>
                <UrbanMap
                  events={NO_EVENTS}
                  initialCenter={
                    latitude.trim() && longitude.trim() && candidateCenter.success
                      ? candidateCenter.data
                      : null
                  }
                  onPickLocation={(lat, lon) =>
                    setPickedLocation({ latitude: lat, longitude: lon })
                  }
                />
              </Suspense>
              <button type="button" disabled={!pickedLocation} onClick={confirmMapLocation}>
                Confirmar localização no mapa
              </button>
            </div>
          )}
          {draft.source === 'pwa_photo' && (
            <>
              <button
                type="button"
                className="secondary"
                disabled={locating || busy}
                onClick={locate}
              >
                <LocateFixed size={16} />
                {locating ? 'Obtendo posição…' : 'Obter posição do dispositivo'}
              </button>
              <p className="muted">
                Use enquanto ainda estiver no local da foto. O horário da posição é registrado
                separadamente.
              </p>
            </>
          )}
          <p className="muted">
            Origem:{' '}
            {draft.source_location === 'gps_device'
              ? 'GPS do dispositivo'
              : draft.source_location === 'exif'
                ? 'GPS EXIF da foto (pendente de validação pelo servidor)'
                : latitude || longitude
                  ? 'informada manualmente'
                  : 'não disponível'}{' '}
            · Precisão:{' '}
            {draft.source_location === 'gps_device' && draft.coordinate?.accuracy_m != null
              ? `${draft.coordinate.accuracy_m.toFixed(1)} m`
              : 'não disponível'}
          </p>
          <label>
            Descreva o problema (opcional)
            <textarea
              rows={4}
              maxLength={500}
              value={draft.note}
              placeholder="Registre o contexto que você observou…"
              onChange={(event) => setDraft((value) => ({ ...value, note: event.target.value }))}
            />
          </label>
          <p aria-live="polite">{draft.note.length}/500 caracteres</p>
          <p className="muted">Observações do usuário não são classificações do modelo.</p>
          <section aria-label="Privacidade do relato">
            <h3>Antes de enviar</h3>
            {privacy ? (
              <>
                <p>{privacy.text}</p>
                <label>
                  <input
                    type="checkbox"
                    checked={acceptedVersion === privacy.version}
                    onChange={(event) =>
                      setAcceptedVersion(event.target.checked ? privacy.version : null)
                    }
                  />
                  Li e aceito o armazenamento da foto e localização conforme este aviso
                </label>
              </>
            ) : (
              <p role="status">
                Aviso de privacidade indisponível. O envio aguarda conexão; o rascunho pode ser
                preservado.
              </p>
            )}
          </section>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy || locating}>
            <Save size={17} /> {busy ? 'Enviando…' : 'Salvar e enviar'}
          </button>
        </section>
      </form>
      <p className="footnote">
        O rascunho fica neste navegador até o envio. Após o envio, a foto original permanece no
        Storage privado e a análise experimental ocorre no backend.
      </p>
    </>
  );
}

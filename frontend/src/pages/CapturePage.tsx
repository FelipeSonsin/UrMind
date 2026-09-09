import { useState, type FormEvent } from 'react';
import { Camera as CameraIcon, Upload, LocateFixed, Save } from 'lucide-react';
import { Camera } from '../components/Camera';
import { Photo } from '../components/Photo';
import { parseCoordinate, coordinateSchema } from '../domain/contracts';
import { drafts, validatePhoto, type CaptureDraft } from '../services/drafts';

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
export function CapturePage({ initial, onSaved }: { initial?: CaptureDraft; onSaved: () => void }) {
  const [draft, setDraft] = useState<CaptureDraft>(() => initial || newDraft());
  const [latitude, setLatitude] = useState(initial?.coordinate?.latitude.toString() || '');
  const [longitude, setLongitude] = useState(initial?.coordinate?.longitude.toString() || '');
  const [camera, setCamera] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [locating, setLocating] = useState(false);
  async function select(file: File, fromCamera = false) {
    setBusy(true);
    setError('');
    try {
      await validatePhoto(file);
      setDraft((value) => ({
        ...value,
        photo: file,
        filename: file.name,
        source: fromCamera ? 'pwa_photo' : 'exif_upload',
        captured_at: fromCamera ? new Date().toISOString() : null,
        coordinate: null,
        source_location: 'unknown',
        location_timestamp: null,
        heading_deg: null,
        speed_mps: null,
      }));
      setLatitude('');
      setLongitude('');
      setCamera(false);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function locate() {
    if (!navigator.geolocation) {
      setError('Geolocalização indisponível neste navegador.');
      return;
    }
    setLocating(true);
    setError('');
    navigator.geolocation.getCurrentPosition(
      (position) => {
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
        setError(
          'Não foi possível obter a localização. Verifique a permissão ou informe as coordenadas.',
        );
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  }
  function manual() {
    setDraft((value) => ({
      ...value,
      source_location: 'manual',
      coordinate: null,
      location_timestamp: null,
      heading_deg: null,
      speed_mps: null,
    }));
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (!draft.photo.size) throw new Error('Selecione ou tire uma foto antes de salvar.');
      const coordinate =
        latitude.trim() || longitude.trim() ? parseCoordinate(latitude, longitude) : null;
      await drafts.save({
        ...draft,
        coordinate:
          coordinate && draft.source_location === 'gps_device' ? draft.coordinate : coordinate,
        source_location: coordinate
          ? draft.source_location === 'gps_device'
            ? 'gps_device'
            : 'manual'
          : 'unknown',
      });
      onSaved();
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
              Leitura de EXIF ainda não conectada. Informe a localização manualmente; a posição
              atual do dispositivo não será atribuída à foto importada.
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
              : latitude || longitude
                ? 'informada manualmente'
                : 'não disponível'}{' '}
            · Precisão:{' '}
            {draft.source_location === 'gps_device' && draft.coordinate?.accuracy_m != null
              ? `${draft.coordinate.accuracy_m.toFixed(1)} m`
              : 'não disponível'}
          </p>
          <label>
            Observações <span className="muted">(opcional)</span>
            <textarea
              rows={4}
              maxLength={2000}
              value={draft.note}
              placeholder="Registre o contexto que você observou…"
              onChange={(event) => setDraft((value) => ({ ...value, note: event.target.value }))}
            />
          </label>
          <p className="muted">Observações do usuário não são classificações do modelo.</p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy || locating}>
            <Save size={17} /> {busy ? 'Salvando…' : 'Salvar rascunho local'}
          </button>
        </section>
      </form>
      <p className="footnote">
        A foto permanece neste navegador. Nenhuma captura é enviada ou classificada nesta fase.
        Limpar os dados do navegador remove os rascunhos.
      </p>
    </>
  );
}

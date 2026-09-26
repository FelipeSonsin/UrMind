/**
 * Device GPS for a photo taken now. The phone's own location service does the
 * work; this module only makes it reliable:
 *
 * - `warmUp()` starts watching as soon as the person heads to the camera, so a
 *   fix is usually ready when the photo comes back;
 * - `currentFix()` reuses a watched fix younger than `FRESH_MS`, otherwise asks
 *   for a high-accuracy reading and falls back to a coarser recent one (indoors a
 *   GNSS fix can take longer than any sensible timeout);
 * - no reading older than `MAX_FIX_AGE_MS` is ever returned: walking or driving,
 *   an old fix belongs to another place, so it is refused instead of attached;
 * - errors carry a message that says how to fix them.
 */

export const FRESH_MS = 30 * 1000;
export const MAX_FIX_AGE_MS = 60 * 1000;
const HIGH_ACCURACY = { enableHighAccuracy: true, timeout: 20_000, maximumAge: FRESH_MS };
const COARSE = { enableHighAccuracy: false, timeout: 20_000, maximumAge: MAX_FIX_AGE_MS };

export class LocationError extends Error {
  constructor(
    message: string,
    readonly reason: 'unsupported' | 'denied' | 'unavailable' | 'stale',
  ) {
    super(message);
  }
}

let latest: GeolocationPosition | null = null;
let watchId: number | null = null;

function geolocation(): Geolocation | null {
  return typeof navigator !== 'undefined' && navigator.geolocation ? navigator.geolocation : null;
}

/** Starts (once) a background watch that keeps the latest fix at hand. */
export function warmUp() {
  const geo = geolocation();
  if (!geo || watchId !== null) return;
  watchId = geo.watchPosition(
    (position) => {
      latest = position;
    },
    () => {
      // A failed watch is not an error to show: `currentFix` asks again.
    },
    HIGH_ACCURACY,
  );
}

export function stopWarmUp() {
  const geo = geolocation();
  if (geo && watchId !== null) geo.clearWatch(watchId);
  watchId = null;
}

/** Warms up without prompting when the site already has permission. */
export async function warmUpIfAllowed() {
  try {
    const status = await navigator.permissions?.query({ name: 'geolocation' });
    if (status?.state === 'granted') warmUp();
  } catch {
    // Permissions API missing (older Safari): wait for the person's action.
  }
}

function read(geo: Geolocation, options: PositionOptions) {
  return new Promise<GeolocationPosition>((resolve, reject) =>
    geo.getCurrentPosition(resolve, reject, options),
  );
}

/** Até onde o GPS do aparelho ainda descreve o lugar de uma foto tirada agora. */
export const PHOTO_FIX_WINDOW_MS = 2 * 60 * 1000;

/**
 * A posição só é da foto se foi lida perto do instante da foto. Uma foto tirada há
 * mais tempo pode ter sido feita em outro lugar: nesse caso o local vem do mapa.
 */
export function fixMatchesPhoto(fixTimestamp: number, capturedAt: string | null): boolean {
  const taken = capturedAt ? Date.parse(capturedAt) : NaN;
  return Number.isFinite(taken) && Math.abs(fixTimestamp - taken) <= PHOTO_FIX_WINDOW_MS;
}

export function isFresh(position: GeolocationPosition | null, now = Date.now()) {
  return position != null && now - position.timestamp <= FRESH_MS;
}

/** Some devices hand back an old cached fix despite `maximumAge`; never pass it on. */
function recent(position: GeolocationPosition, now = Date.now()): GeolocationPosition {
  if (now - position.timestamp > MAX_FIX_AGE_MS)
    throw new LocationError(
      'O aparelho devolveu uma posição antiga. Tente de novo em área aberta ou marque o local no mapa.',
      'stale',
    );
  return position;
}

export async function currentFix(): Promise<GeolocationPosition> {
  const geo = geolocation();
  if (!geo)
    throw new LocationError('Este navegador não oferece localização do aparelho.', 'unsupported');
  if (isFresh(latest)) return latest!;
  try {
    latest = recent(await read(geo, HIGH_ACCURACY));
    return latest;
  } catch (error) {
    // A stale high-accuracy answer is not final: the coarse reading below may be fresh.
    const code = (error as GeolocationPositionError | undefined)?.code;
    if (code === 1)
      throw new LocationError(
        'Localização bloqueada para este site. Libere em Configurações do navegador › Localização e toque em "Tentar de novo".',
        'denied',
      );
  }
  try {
    latest = recent(await read(geo, COARSE));
    return latest;
  } catch (error) {
    if (error instanceof LocationError) throw error;
    const code = (error as GeolocationPositionError | undefined)?.code;
    throw new LocationError(
      code === 1
        ? 'Localização bloqueada para este site. Libere em Configurações do navegador › Localização.'
        : 'O aparelho não conseguiu a posição agora. Ative o GPS/localização do celular e tente de novo, ou marque o local no mapa.',
      code === 1 ? 'denied' : 'unavailable',
    );
  }
}

/**
 * Device GPS for a photo taken now. The phone's own location service does the
 * work; this module only makes it reliable:
 *
 * - `warmUp()` starts watching as soon as the person heads to the camera, so a
 *   fix is usually ready when the photo comes back;
 * - `currentFix()` reuses a fix younger than `FRESH_MS`, otherwise asks for a
 *   high-accuracy reading and falls back to a coarser recent one (indoors a
 *   GNSS fix can take longer than any sensible timeout);
 * - errors carry a message that says how to fix them.
 */

export const FRESH_MS = 2 * 60 * 1000;
const HIGH_ACCURACY = { enableHighAccuracy: true, timeout: 20_000, maximumAge: 30_000 };
const COARSE = { enableHighAccuracy: false, timeout: 20_000, maximumAge: 5 * 60 * 1000 };

export class LocationError extends Error {
  constructor(
    message: string,
    readonly reason: 'unsupported' | 'denied' | 'unavailable',
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

export function isFresh(position: GeolocationPosition | null, now = Date.now()) {
  return position != null && now - position.timestamp <= FRESH_MS;
}

export async function currentFix(): Promise<GeolocationPosition> {
  const geo = geolocation();
  if (!geo)
    throw new LocationError('Este navegador não oferece localização do aparelho.', 'unsupported');
  if (isFresh(latest)) return latest!;
  try {
    latest = await read(geo, HIGH_ACCURACY);
    return latest;
  } catch (error) {
    const code = (error as GeolocationPositionError | undefined)?.code;
    if (code === 1)
      throw new LocationError(
        'Localização bloqueada para este site. Libere em Configurações do navegador › Localização e toque em "Tentar localizar de novo".',
        'denied',
      );
  }
  try {
    latest = await read(geo, COARSE);
    return latest;
  } catch (error) {
    const code = (error as GeolocationPositionError | undefined)?.code;
    throw new LocationError(
      code === 1
        ? 'Localização bloqueada para este site. Libere em Configurações do navegador › Localização.'
        : 'O aparelho não conseguiu a posição agora. Ative o GPS/localização do celular e tente de novo, ou marque o local no mapa.',
      code === 1 ? 'denied' : 'unavailable',
    );
  }
}

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type Success = (position: GeolocationPosition) => void;
type Failure = (error: { code: number }) => void;

function fix(latitude: number, timestamp = Date.now()) {
  return {
    coords: { latitude, longitude: -46.63, accuracy: 12, heading: null, speed: null },
    timestamp,
  } as unknown as GeolocationPosition;
}

/** Each call to getCurrentPosition consumes the next scripted outcome. */
function stubGeolocation(outcomes: Array<GeolocationPosition | { code: number }>) {
  const options: PositionOptions[] = [];
  const geolocation = {
    getCurrentPosition: vi.fn((ok: Success, fail: Failure, opts: PositionOptions) => {
      options.push(opts);
      const next = outcomes.shift();
      if (next && 'coords' in next) ok(next);
      else fail(next ?? { code: 2 });
    }),
    watchPosition: vi.fn(() => 7),
    clearWatch: vi.fn(),
  };
  vi.stubGlobal('navigator', { geolocation });
  return { geolocation, options };
}

async function freshModule() {
  vi.resetModules();
  return import('./deviceLocation');
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe('currentFix', () => {
  it('asks for a high-accuracy reading first', async () => {
    const { options } = stubGeolocation([fix(-23.55)]);
    const { currentFix } = await freshModule();
    await expect(currentFix()).resolves.toMatchObject({ coords: { latitude: -23.55 } });
    expect(options[0].enableHighAccuracy).toBe(true);
  });

  it('falls back to a coarse recent fix when high accuracy times out indoors', async () => {
    const { options } = stubGeolocation([{ code: 3 }, fix(-22.9)]);
    const { currentFix } = await freshModule();
    await expect(currentFix()).resolves.toMatchObject({ coords: { latitude: -22.9 } });
    expect(options[1].enableHighAccuracy).toBe(false);
    expect(options[1].maximumAge).toBeGreaterThan(0);
  });

  it('explains how to unblock a denied permission without retrying', async () => {
    const { geolocation } = stubGeolocation([{ code: 1 }]);
    const { currentFix } = await freshModule();
    await expect(currentFix()).rejects.toMatchObject({ reason: 'denied' });
    expect(geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
  });

  it('points to the map when the device cannot get any position', async () => {
    stubGeolocation([{ code: 2 }, { code: 2 }]);
    const { currentFix } = await freshModule();
    await expect(currentFix()).rejects.toThrow(/marque o local no mapa/);
  });

  it('reuses a fix younger than thirty seconds instead of asking again', async () => {
    const { geolocation } = stubGeolocation([fix(-23.55)]);
    const { currentFix } = await freshModule();
    await currentFix();
    await currentFix();
    expect(geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
  });

  it('asks again once the kept fix is older than thirty seconds', async () => {
    const now = Date.now();
    const { geolocation } = stubGeolocation([fix(-23.55, now - 40_000), fix(-22.9, now)]);
    const { currentFix } = await freshModule();
    await currentFix();
    await expect(currentFix()).resolves.toMatchObject({ coords: { latitude: -22.9 } });
    expect(geolocation.getCurrentPosition).toHaveBeenCalledTimes(2);
  });

  it('refuses a cached reading from another place instead of attaching it', async () => {
    const old = Date.now() - 5 * 60_000;
    stubGeolocation([fix(-23.55, old), fix(-23.55, old)]);
    const { currentFix } = await freshModule();
    await expect(currentFix()).rejects.toMatchObject({ reason: 'stale' });
  });

  it('tries the coarse reading when the high-accuracy one comes back stale', async () => {
    const now = Date.now();
    stubGeolocation([fix(-23.55, now - 5 * 60_000), fix(-22.9, now)]);
    const { currentFix } = await freshModule();
    await expect(currentFix()).resolves.toMatchObject({ coords: { latitude: -22.9 } });
  });

  it('never asks the coarse fallback for a reading older than one minute', async () => {
    const { options } = stubGeolocation([{ code: 3 }, fix(-22.9)]);
    const { currentFix, MAX_FIX_AGE_MS } = await freshModule();
    await currentFix();
    expect(options.every((option) => (option.maximumAge ?? 0) <= MAX_FIX_AGE_MS)).toBe(true);
  });

  it('reports a browser without geolocation', async () => {
    vi.stubGlobal('navigator', {});
    const { currentFix } = await freshModule();
    await expect(currentFix()).rejects.toMatchObject({ reason: 'unsupported' });
  });
});

describe('fixMatchesPhoto', () => {
  it('ties the device position to the moment of the photo only', async () => {
    const { fixMatchesPhoto } = await freshModule();
    const taken = '2026-09-26T12:00:00.000Z';
    const at = Date.parse(taken);
    expect(fixMatchesPhoto(at + 90_000, taken)).toBe(true);
    expect(fixMatchesPhoto(at - 90_000, taken)).toBe(true);
    // Foto de cinco minutos atrás: o aparelho pode já estar em outro lugar.
    expect(fixMatchesPhoto(at + 5 * 60_000, taken)).toBe(false);
    expect(fixMatchesPhoto(at, null)).toBe(false);
    expect(fixMatchesPhoto(at, 'not a date')).toBe(false);
  });
});

describe('isFresh', () => {
  it('accepts thirty seconds and rejects older fixes', async () => {
    const { isFresh } = await freshModule();
    const now = 1_000_000;
    expect(isFresh(fix(0, now - 29_000), now)).toBe(true);
    expect(isFresh(fix(0, now - 31_000), now)).toBe(false);
    expect(isFresh(null, now)).toBe(false);
  });
});

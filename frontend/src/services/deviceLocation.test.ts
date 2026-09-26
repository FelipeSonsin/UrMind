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

  it('reuses a fix younger than two minutes instead of asking again', async () => {
    const { geolocation } = stubGeolocation([fix(-23.55)]);
    const { currentFix } = await freshModule();
    await currentFix();
    await currentFix();
    expect(geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
  });

  it('reports a browser without geolocation', async () => {
    vi.stubGlobal('navigator', {});
    const { currentFix } = await freshModule();
    await expect(currentFix()).rejects.toMatchObject({ reason: 'unsupported' });
  });
});

describe('isFresh', () => {
  it('accepts two minutes and rejects older fixes', async () => {
    const { isFresh } = await freshModule();
    const now = 1_000_000;
    expect(isFresh(fix(0, now - 119_000), now)).toBe(true);
    expect(isFresh(fix(0, now - 121_000), now)).toBe(false);
    expect(isFresh(null, now)).toBe(false);
  });
});

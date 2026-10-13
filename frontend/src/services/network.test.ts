import { afterEach, describe, expect, it, vi } from 'vitest';
import { isSlowResponse, oldestPendingRead, SLOW_AFTER_MS, trackRead } from './network';

afterEach(() => {
  vi.useRealTimers();
});

describe('espera longa pela API (aviso de espera)', () => {
  it('avisa só depois de 6 s sem resposta e para quando a resposta chega', () => {
    vi.useFakeTimers();
    const done = trackRead();
    vi.advanceTimersByTime(SLOW_AFTER_MS - 100);
    expect(isSlowResponse()).toBe(false);
    vi.advanceTimersByTime(200);
    expect(isSlowResponse()).toBe(true);
    done();
    expect(isSlowResponse()).toBe(false);
    expect(oldestPendingRead()).toBeNull();
  });

  it('não pisca quando a leitura mais antiga termina e outra, também demorada, continua', () => {
    vi.useFakeTimers();
    const first = trackRead();
    vi.advanceTimersByTime(1000);
    const second = trackRead();
    vi.advanceTimersByTime(SLOW_AFTER_MS);
    expect(isSlowResponse()).toBe(true);
    first();
    expect(isSlowResponse()).toBe(true);
    second();
    expect(isSlowResponse()).toBe(false);
  });

  it('uma leitura que acabou de começar não conta como demorada antes do tempo', () => {
    vi.useFakeTimers();
    const old = trackRead();
    vi.advanceTimersByTime(SLOW_AFTER_MS + 1000);
    const fresh = trackRead();
    old();
    expect(isSlowResponse()).toBe(false);
    vi.advanceTimersByTime(SLOW_AFTER_MS);
    expect(isSlowResponse()).toBe(true);
    fresh();
    expect(isSlowResponse()).toBe(false);
  });
});

import { describe, expect, it } from 'vitest';
import { activeCapabilities, automaticClasses, robotCameraReady } from './capabilities';

describe('registro de capacidades', () => {
  it('não anuncia classes automáticas sem modelo registrado', () => {
    expect(activeCapabilities.supportedAutomaticClasses).toEqual([]);
  });

  it('usa somente as classes autorizadas pela taxonomia servida', () => {
    expect(automaticClasses(['URMIND_ROAD_D40'])).toEqual(['URMIND_ROAD_D40']);
    expect(automaticClasses(['URMIND_ROAD_D00', 'URMIND_ROAD_D40'])).toEqual([
      'URMIND_ROAD_D40',
      'URMIND_ROAD_D00',
    ]);
    expect(automaticClasses([])).toEqual([]);
  });
});

describe('câmera do robô no registro', () => {
  const env = {
    VITE_SUPABASE_URL: 'https://projeto.supabase.co',
    VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_x',
    VITE_ROBOT_CAMERA_STUN_URL: 'stun:stun.cloudflare.com:3478',
  };
  it('só é capacidade pública com Supabase e STUN configurados', () => {
    expect(robotCameraReady(env)).toBe(true);
    expect(robotCameraReady({ ...env, VITE_ROBOT_CAMERA_STUN_URL: '' })).toBe(false);
    expect(robotCameraReady({ ...env, VITE_ROBOT_CAMERA_STUN_URL: 'turn:x.example:3478' })).toBe(
      false,
    );
    expect(robotCameraReady({ ...env, VITE_SUPABASE_URL: undefined })).toBe(false);
  });
});

import { describe, expect, it } from 'vitest';
import { activeCapabilities, automaticClasses, robotCameraReady } from './capabilities';

describe('registro de capacidades', () => {
  it('a detecção automática reconhece só as quatro classes de pavimento', () => {
    expect([...activeCapabilities.supportedAutomaticClasses].sort()).toEqual([
      'URMIND_ROAD_D00',
      'URMIND_ROAD_D10',
      'URMIND_ROAD_D20',
      'URMIND_ROAD_D40',
    ]);
    for (const future of ['URMIND_FALLEN_TREE', 'URMIND_ROAD_DEBRIS', 'URMIND_FLOODED_ROAD'])
      expect(activeCapabilities.supportedAutomaticClasses).not.toContain(future);
  });

  it('usa a taxonomia servida quando existe e o registro só como reserva', () => {
    expect(automaticClasses(['URMIND_ROAD_D40'])).toEqual(['URMIND_ROAD_D40']);
    expect(automaticClasses(['URMIND_ROAD_D00', 'URMIND_ROAD_D40'])).toEqual([
      'URMIND_ROAD_D40',
      'URMIND_ROAD_D00',
    ]);
    expect(automaticClasses([])).toBe(activeCapabilities.supportedAutomaticClasses);
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

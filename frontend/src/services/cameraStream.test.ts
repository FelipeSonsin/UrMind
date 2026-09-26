import { afterEach, describe, expect, it, vi } from 'vitest';
import { ROBOT_PHONE_CONSTRAINTS } from '../domain/robotCamera';
import { CameraError, TARGET_CAMERA_FPS, cameraConstraints, openCamera } from './cameraStream';

const original = Object.getOwnPropertyDescriptor(globalThis, 'navigator');

function install(getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>) {
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { mediaDevices: { getUserMedia } },
  });
}

afterEach(() => {
  if (original) Object.defineProperty(globalThis, 'navigator', original);
});

const stream = {} as MediaStream;
const failure = (name: string) => Object.assign(new Error(name), { name });

describe('câmeras a 60 quadros por segundo', () => {
  it('toda câmera pede pelo menos 60 fps primeiro e nunca pede microfone', async () => {
    const getUserMedia = vi.fn(async () => stream);
    install(getUserMedia);
    await expect(openCamera()).resolves.toBe(stream);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(getUserMedia).toHaveBeenCalledWith({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
        frameRate: { min: 60, ideal: 60 },
      },
      audio: false,
    });
    expect(TARGET_CAMERA_FPS).toBe(60);
  });

  it('câmera sem modo de 60 fps não falha: usa a melhor que tiver, ainda preferindo 60', async () => {
    const getUserMedia = vi
      .fn<(constraints: MediaStreamConstraints) => Promise<MediaStream>>()
      .mockRejectedValueOnce(failure('OverconstrainedError'))
      .mockResolvedValueOnce(stream);
    install(getUserMedia);
    await expect(openCamera('cam-b')).resolves.toBe(stream);
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(getUserMedia.mock.calls[1][0]).toEqual({
      video: {
        deviceId: { exact: 'cam-b' },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
        frameRate: { ideal: 60 },
      },
      audio: false,
    });
  });

  it('permissão negada não vira nova tentativa', async () => {
    const getUserMedia = vi.fn(async () => {
      throw failure('NotAllowedError');
    });
    install(getUserMedia);
    await expect(openCamera()).rejects.toBeInstanceOf(CameraError);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it('o celular do robô usa o perfil dele com a mesma regra de 60 fps', async () => {
    const getUserMedia = vi.fn(async () => stream);
    install(getUserMedia);
    await openCamera(undefined, ROBOT_PHONE_CONSTRAINTS.video);
    expect(getUserMedia).toHaveBeenCalledWith(
      cameraConstraints(ROBOT_PHONE_CONSTRAINTS.video, true),
    );
    expect(cameraConstraints(ROBOT_PHONE_CONSTRAINTS.video, true).video).toMatchObject({
      facingMode: 'environment',
      width: { ideal: 1280 },
      height: { ideal: 720 },
      frameRate: { min: 60, ideal: 60 },
    });
  });
});

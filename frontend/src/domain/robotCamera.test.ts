import { describe, expect, it } from 'vitest';
import {
  MAX_ICE_RESTARTS,
  PAIRING_TTL_MS,
  RECONNECT_GRACE_MS,
  ROBOT_PHONE_CONSTRAINTS,
  ROBOT_SEND_MAX_BITRATE,
  ROBOT_SEND_MAX_FPS,
  RobotCameraConfigError,
  channelName,
  createPairing,
  formatRemaining,
  linkVerdict,
  pairingCode,
  pairingExpired,
  pairingLink,
  parsePairingHash,
  readSignal,
  robotIceServers,
  visionStatus,
} from './robotCamera';

describe('pareamento da câmera do robô', () => {
  it('cria sessão e token aleatórios que expiram em 5 minutos', () => {
    const now = Date.parse('2026-09-26T12:00:00Z');
    const a = createPairing(now);
    const b = createPairing(now);
    expect(a.sessionId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(a.token).toMatch(/^[A-Za-z0-9_-]{32}$/);
    expect(a.sessionId).not.toBe(b.sessionId);
    expect(a.token).not.toBe(b.token);
    expect(a.expiresAt - now).toBe(PAIRING_TTL_MS);
    expect(PAIRING_TTL_MS).toBe(5 * 60_000);
    expect(pairingExpired(a, now + PAIRING_TTL_MS - 1)).toBe(false);
    expect(pairingExpired(a, now + PAIRING_TTL_MS)).toBe(true);
  });

  it('o QR abre a página do celular na origem atual, sem domínio fixo', () => {
    const pairing = createPairing();
    const link = pairingLink('http://192.168.0.10:4173/', pairing);
    expect(link).toBe(
      `http://192.168.0.10:4173/#/camera-robo/celular?session=${pairing.sessionId}&token=${pairing.token}`,
    );
    const other = pairingLink('https://urmind.example', pairing);
    expect(other.startsWith('https://urmind.example/#/camera-robo/celular?')).toBe(true);
    expect(parsePairingHash(new URL(link).hash)).toEqual({
      sessionId: pairing.sessionId,
      token: pairing.token,
    });
  });

  it('recusa endereço do celular fora do formato', () => {
    const { sessionId, token } = createPairing();
    const hash = (session: string, key: string) =>
      `#/camera-robo/celular?session=${session}&token=${key}`;
    expect(parsePairingHash(hash(sessionId, token))).not.toBeNull();
    expect(parsePairingHash(hash('nao-e-uuid', token))).toBeNull();
    expect(parsePairingHash(hash(sessionId, 'curto'))).toBeNull();
    expect(parsePairingHash(hash(sessionId, `${token}<script>`))).toBeNull();
    expect(parsePairingHash(`#/camera-robo?session=${sessionId}&token=${token}`)).toBeNull();
    expect(parsePairingHash('#/camera-robo/celular')).toBeNull();
  });

  it('canal temporário por sessão e código curto igual nos dois aparelhos', () => {
    const pairing = createPairing();
    expect(channelName(pairing.sessionId)).toBe(`robot-camera:${pairing.sessionId}`);
    expect(pairingCode(pairing.token)).toMatch(/^[A-Z2-9]{4}$/);
    expect(pairingCode(pairing.token)).toBe(pairingCode(pairing.token));
    expect(formatRemaining(PAIRING_TTL_MS)).toBe('5:00');
    expect(formatRemaining(61_000)).toBe('1:01');
    expect(formatRemaining(-5)).toBe('0:00');
  });
});

describe('configuração do STUN', () => {
  it('usa só a variável de ambiente, sem credencial nem valor embutido', () => {
    expect(robotIceServers('stun:stun.cloudflare.com:3478')).toEqual([
      { urls: 'stun:stun.cloudflare.com:3478' },
    ]);
    expect(() => robotIceServers(undefined)).toThrow(RobotCameraConfigError);
    expect(() => robotIceServers('  ')).toThrow(/VITE_ROBOT_CAMERA_STUN_URL/);
    expect(() => robotIceServers('turn:turn.example.com:3478')).toThrow(RobotCameraConfigError);
    expect(() => robotIceServers('stun:user:pass@stun.example.com')).toThrow(
      RobotCameraConfigError,
    );
  });

  it('câmera traseira em 720p, microfone desligado e envio até 60 fps', () => {
    expect(ROBOT_PHONE_CONSTRAINTS.audio).toBe(false);
    expect(ROBOT_PHONE_CONSTRAINTS.video).toEqual({
      facingMode: 'environment',
      width: { ideal: 1280 },
      height: { ideal: 720 },
    });
    expect(ROBOT_SEND_MAX_FPS).toBe(60);
    // 720p a 60 fps nítido pede alguns megabits; abaixo de ~3 Mbps a imagem borra.
    expect(ROBOT_SEND_MAX_BITRATE).toBeGreaterThanOrEqual(3_000_000);
  });
});

describe('sinalização', () => {
  const token = createPairing().token;
  const peer = '3f8b9d3a-2f0c-4f1e-9b1a-4f6a0d5e7c11';

  it('o notebook aceita só mensagens do celular com o token do pareamento', () => {
    const offer = { kind: 'offer', from: 'sender', token, peer, sdp: 'v=0' };
    expect(readSignal(offer, { role: 'receiver', token })).toEqual(offer);
    expect(readSignal({ ...offer, token: 'x'.repeat(32) }, { role: 'receiver', token })).toBeNull();
    // Mensagem do próprio papel (eco) é ignorada.
    expect(readSignal(offer, { role: 'sender', token })).toBeNull();
    expect(readSignal({ ...offer, kind: 'video-frame' }, { role: 'receiver', token })).toBeNull();
    expect(readSignal({ ...offer, sdp: '' }, { role: 'receiver', token })).toBeNull();
  });

  it('troca offer, answer e candidatos ICE', () => {
    const answer = { kind: 'answer', from: 'receiver', token, peer, sdp: 'v=0' };
    expect(readSignal(answer, { role: 'sender', token })?.kind).toBe('answer');
    const ice = {
      kind: 'ice-candidate',
      from: 'sender',
      token,
      peer,
      candidate: { candidate: 'candidate:1 1 udp 1 10.0.0.2 5000 typ host', sdpMid: '0' },
    };
    expect(readSignal(ice, { role: 'receiver', token })?.kind).toBe('ice-candidate');
    for (const kind of ['sender-ready', 'disconnect'])
      expect(
        readSignal({ kind, from: 'sender', token, peer }, { role: 'receiver', token }),
      ).not.toBeNull();
  });

  it('erro do notebook chega ao celular sem revelar o token', () => {
    const error = { kind: 'error', from: 'receiver', token: '', peer, reason: 'invalid-token' };
    expect(readSignal(error, { role: 'sender', token })?.kind).toBe('error');
    expect(readSignal({ ...error, reason: 'outro' }, { role: 'sender', token })).toBeNull();
  });
});

describe('queda e reconexão', () => {
  it('queda temporária vira "reconectando" e só depois da tolerância vira perda', () => {
    expect(linkVerdict('connected', null)).toBe('connected');
    expect(linkVerdict('connecting', null)).toBe('connecting');
    expect(linkVerdict('disconnected', 0)).toBe('reconnecting');
    expect(linkVerdict('failed', RECONNECT_GRACE_MS - 1)).toBe('reconnecting');
    expect(linkVerdict('disconnected', RECONNECT_GRACE_MS)).toBe('lost');
    expect(linkVerdict('closed', null)).toBe('lost');
  });

  it('as tentativas automáticas têm limite: nada de laço infinito', () => {
    expect(MAX_ICE_RESTARTS).toBeGreaterThan(0);
    expect(MAX_ICE_RESTARTS).toBeLessThanOrEqual(3);
  });
});

describe('estado da visão computacional no vídeo remoto', () => {
  const base = {
    model: 'ready' as const,
    connected: true,
    videoReady: true,
    detecting: false,
    paused: false,
  };

  it('"Analisando" só com o worker pronto e a detecção ligada', () => {
    expect(visionStatus({ ...base, detecting: true })).toEqual({
      label: 'Analisando vídeo em tempo real',
      tone: 'active',
    });
    // Modelo ainda carregando: nunca "pronta" nem "analisando" antes da hora.
    expect(visionStatus({ ...base, model: 'loading', detecting: true })).toEqual({
      label: 'Preparando detecção…',
      tone: 'idle',
    });
    expect(visionStatus({ ...base, model: 'available', detecting: true }).label).toBe(
      'Preparando detecção…',
    );
  });

  it('sem imagem no vídeo, aguarda o vídeo; modelo indisponível é dito com clareza', () => {
    expect(visionStatus({ ...base, videoReady: false, detecting: true }).label).toBe(
      'Aguardando vídeo',
    );
    for (const model of ['unavailable', 'failed'] as const)
      expect(visionStatus({ ...base, model, detecting: true })).toEqual({
        label: 'Detecção indisponível',
        tone: 'error',
      });
  });

  it('pausado e pronto são estados distintos', () => {
    expect(visionStatus({ ...base, paused: true }).label).toBe('Pausado');
    expect(visionStatus(base).label).toBe('Detecção pronta');
    expect(visionStatus({ ...base, model: 'checking' }).label).toBe('Verificando a detecção…');
  });
});

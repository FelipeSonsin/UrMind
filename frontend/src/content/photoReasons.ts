/**
 * Motivo da checagem automática da foto (`photo_gate.reasons`), com o mesmo nome em todas
 * as telas: Painel, revisão do relato e histórico de quem enviou.
 */
const PHOTO_REASONS: Record<string, string> = {
  resolution: 'Foto pequena',
  blur: 'Foto tremida ou desfocada',
  underexposed: 'Foto escura',
  overexposed: 'Foto clara demais',
  duplicate: 'Foto repetida',
  scene: 'Foto não mostra a rua',
  non_urban_scene: 'Foto não mostra a rua',
  face: 'Rosto na foto',
  face_large: 'Rosto em primeiro plano',
  dominant_face: 'Rosto em primeiro plano',
};

/** Nome do motivo; um código novo da API aparece como "Outro motivo", nunca como código. */
export function photoReason(code: string): string {
  return PHOTO_REASONS[code] ?? 'Outro motivo';
}

/** Vários motivos numa frase: "Foto escura, foto tremida ou desfocada". */
export function photoReasonList(codes: string[]): string {
  return codes
    .map((code, index) => (index ? photoReason(code).toLowerCase() : photoReason(code)))
    .join(', ');
}

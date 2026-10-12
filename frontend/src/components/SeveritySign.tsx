import { KEYLINE, SEVERITY_SIGNS, SHAPE_PATHS, WARNING_INSET } from './signShapes';

/** O símbolo da gravidade, igual ao do mapa e da legenda: forma, cor e contorno. */
export function SeveritySign({ level }: { level: string }) {
  const sign = SEVERITY_SIGNS[level as keyof typeof SEVERITY_SIGNS] ?? SEVERITY_SIGNS.unknown;
  const ring = sign.shape === 'ring';
  return (
    <svg className="severity-sign" viewBox="-2 -2 36 36" aria-hidden="true" focusable="false">
      <path
        d={SHAPE_PATHS[sign.shape]}
        fill={ring ? '#ffffff' : sign.fill}
        stroke={ring ? sign.fill : KEYLINE}
        strokeWidth={ring ? 3 : 2.5}
        strokeLinejoin="round"
      />
      {sign.shape === 'warning' && (
        <path d={WARNING_INSET} fill="none" stroke={KEYLINE} strokeWidth={1.6} />
      )}
    </svg>
  );
}

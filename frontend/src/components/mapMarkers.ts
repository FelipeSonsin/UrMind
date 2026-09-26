import { severityOf } from '../domain/public';
import { MAP_DESIGN, type MarkerShape } from '../mapDesign';

/**
 * Sign shapes on a 32×32 grid. The same paths draw the map marker (canvas) and
 * the legend swatch (SVG), so the legend can never drift from the map.
 */
export const SHAPE_PATHS: Record<MarkerShape, string> = {
  warning: 'M16 3 L30 28 L2 28 Z',
  triangle: 'M16 3 L30 28 L2 28 Z',
  square: 'M7 4 H25 A3 3 0 0 1 28 7 V25 A3 3 0 0 1 25 28 H7 A3 3 0 0 1 4 25 V7 A3 3 0 0 1 7 4 Z',
  circle: 'M3 16 A13 13 0 1 0 29 16 A13 13 0 1 0 3 16 Z',
  ring: 'M3 16 A13 13 0 1 0 29 16 A13 13 0 1 0 3 16 Z',
};
/** Inner rule that turns a triangle into a warning plate. */
export const WARNING_INSET = 'M16 9.5 L25 25 L7 25 Z';
/** Where the family glyph sits inside each shape (triangles are bottom-heavy). */
const GLYPH_CENTER: Record<MarkerShape, [number, number]> = {
  warning: [16, 20],
  triangle: [16, 20],
  square: [16, 16],
  circle: [16, 16],
  ring: [16, 16],
};

export interface MarkerStyle {
  shape: MarkerShape;
  fill: string;
  glyph: string;
}

export function markerStyle(marker: {
  report_status?: string | null;
  status?: string | null;
  severity?: string | null;
}): MarkerStyle {
  const { markers } = MAP_DESIGN;
  const statusColors: Record<string, string> = markers.reportStatus;
  const reportColor = statusColors[marker.report_status ?? marker.status ?? ''];
  if (marker.report_status && reportColor)
    return { shape: 'circle', fill: reportColor, glyph: '#ffffff' };
  const level = severityOf(marker.severity).level as keyof typeof markers.severity;
  return markers.severity[level] ?? markers.severity.unknown;
}

function drawFamilyGlyph(context: CanvasRenderingContext2D, family: string) {
  context.beginPath();
  if (family.includes('VEGETATION')) {
    context.moveTo(16, 4);
    context.lineTo(6, 22);
    context.lineTo(26, 22);
    context.closePath();
    context.moveTo(16, 22);
    context.lineTo(16, 29);
  } else if (family === 'ROAD_SURFACE') {
    context.moveTo(9, 4);
    context.lineTo(9, 28);
    context.moveTo(23, 4);
    context.lineTo(23, 28);
    context.moveTo(16, 6);
    context.lineTo(16, 13);
    context.moveTo(16, 20);
    context.lineTo(16, 27);
  } else if (family === 'DRAINAGE') {
    for (const y of [9, 16, 23]) {
      context.moveTo(4, y);
      context.bezierCurveTo(10, y - 7, 22, y + 7, 28, y);
    }
  } else if (family === 'TRAFFIC_INFRASTRUCTURE') {
    context.moveTo(16, 4);
    context.lineTo(4, 27);
    context.lineTo(28, 27);
    context.closePath();
  } else if (family === 'PEDESTRIAN_INFRASTRUCTURE') {
    context.arc(16, 6, 3, 0, Math.PI * 2);
    context.moveTo(16, 11);
    context.lineTo(16, 21);
    context.moveTo(7, 15);
    context.lineTo(25, 15);
    context.moveTo(8, 29);
    context.lineTo(16, 21);
    context.lineTo(24, 29);
  } else if (family === 'WASTE_OBSTRUCTION') {
    context.rect(8, 10, 16, 18);
    context.moveTo(5, 7);
    context.lineTo(27, 7);
  } else {
    context.rect(7, 7, 18, 18);
  }
  context.stroke();
}

export function markerImageId(style: MarkerStyle, family: string) {
  return `marker:${style.shape}:${style.fill}:${style.glyph}:${family}`;
}

/** Rasterises one sign at 2× for a crisp icon on high-density screens. */
export function markerImage(style: MarkerStyle, family: string): ImageData | null {
  const pixels = MAP_DESIGN.markers.size * 2;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = pixels;
  const context = canvas.getContext('2d');
  if (!context || typeof Path2D === 'undefined') return null;
  const scale = pixels / 32;
  context.scale(scale, scale);
  context.lineJoin = 'round';
  const outline = new Path2D(SHAPE_PATHS[style.shape]);
  // White keyline first so every sign separates from any basemap colour.
  context.strokeStyle = '#ffffff';
  context.lineWidth = 3.5;
  context.stroke(outline);
  if (style.shape === 'ring') {
    context.fillStyle = '#ffffff';
    context.fill(outline);
    context.strokeStyle = style.fill;
    context.lineWidth = 3;
    context.stroke(outline);
  } else {
    context.fillStyle = style.fill;
    context.fill(outline);
  }
  if (style.shape === 'warning') {
    context.strokeStyle = '#ffffff';
    context.lineWidth = 1.4;
    context.stroke(new Path2D(WARNING_INSET));
  }
  const [x, y] = GLYPH_CENTER[style.shape];
  const glyphScale = style.shape === 'warning' ? 0.3 : 0.42;
  context.translate(x - 16 * glyphScale, y - 16 * glyphScale);
  context.scale(glyphScale, glyphScale);
  context.strokeStyle = style.glyph;
  context.fillStyle = style.glyph;
  context.lineWidth = 3.4;
  context.lineCap = 'round';
  drawFamilyGlyph(context, family);
  context.setTransform(1, 0, 0, 1, 0, 0);
  return context.getImageData(0, 0, pixels, pixels);
}

import React from 'react';
import {COLORS} from '../../design-system/tokens';

const toRgba = (color: string, alpha: number) => {
  if (color.startsWith('#')) {
    const hex = color.slice(1);
    const normalized =
      hex.length === 3
        ? hex.split('').map((char) => char + char).join('')
        : hex;

    if (normalized.length === 6) {
      const r = parseInt(normalized.slice(0, 2), 16);
      const g = parseInt(normalized.slice(2, 4), 16);
      const b = parseInt(normalized.slice(4, 6), 16);

      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
  }

  if (color.startsWith('rgb(')) {
    return color.replace('rgb(', 'rgba(').replace(')', `, ${alpha})`);
  }

  if (color.startsWith('rgba(')) {
    return color.replace(/rgba\(([^)]+),\s*[^,]+\)$/, `rgba($1, ${alpha})`);
  }

  return color;
};

export interface VerticalBackgroundProps {
  gridOpacity?: number;
  gridColor?: string;
  gridSize?: number;
  gridStrokeWidth?: number;
  gridBlur?: number;
  fadeTop?: number;
  fadeBottom?: number;
  glowColor?: string;
  glowTop?: string;
  glowSize?: number;
  showGlow?: boolean;
}

export const VerticalBackground: React.FC<VerticalBackgroundProps> = ({
  gridOpacity = 0.1,
  gridColor = COLORS.text,
  gridSize = 60,
  gridStrokeWidth = 0.8,
  gridBlur = 0,
  fadeTop = 0,
  fadeBottom = 0,
  glowColor = COLORS.primary,
  glowTop = '30%',
  glowSize = 500,
  showGlow = true,
}) => {
  const gridThickness = Math.max(0.5, gridStrokeWidth);
  const coreThickness = Math.max(0.36, gridThickness * 0.62);
  const featherEnd = Math.max(1.8, gridThickness * 2.8);
  const fadeMaskTop = Math.max(0, fadeTop);
  const fadeMaskBottom = Math.max(0, fadeBottom);
  const topFeather = Math.max(16, Math.round(fadeMaskTop * 0.42));
  const bottomFeather = Math.max(16, Math.round(fadeMaskBottom * 0.42));
  const coreColor = toRgba(gridColor, 0.9);
  const featherColor = toRgba(gridColor, 0.34);
  const transparentColor = toRgba(gridColor, 0);
  const gridMask =
    fadeMaskTop > 0 || fadeMaskBottom > 0
      ? `linear-gradient(to bottom,
          ${fadeMaskTop > 0 ? 'transparent 0px,' : 'black 0px,'}
          ${fadeMaskTop > 0 ? `rgba(0, 0, 0, 0.26) ${topFeather}px,` : ''}
          ${fadeMaskTop > 0 ? `black ${fadeMaskTop}px,` : ''}
          ${fadeMaskBottom > 0 ? `black calc(100% - ${fadeMaskBottom}px),` : 'black 100%,'}
          ${fadeMaskBottom > 0 ? `rgba(0, 0, 0, 0.26) calc(100% - ${bottomFeather}px),` : ''}
          ${fadeMaskBottom > 0 ? 'transparent 100%)' : 'black 100%)'}`
      : undefined;

  return (
    <>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          pointerEvents: 'none',
          opacity: gridOpacity,
          backgroundImage: `
            linear-gradient(to right, ${coreColor} 0px, ${coreColor} ${coreThickness}px, ${featherColor} ${gridThickness}px, ${transparentColor} ${featherEnd}px),
            linear-gradient(to bottom, ${coreColor} 0px, ${coreColor} ${coreThickness}px, ${featherColor} ${gridThickness}px, ${transparentColor} ${featherEnd}px)
          `,
          backgroundSize: `${gridSize}px ${gridSize}px`,
          backgroundPosition: '0 0',
          filter: gridBlur > 0 ? `blur(${gridBlur}px)` : undefined,
          WebkitMaskImage: gridMask,
          maskImage: gridMask,
          WebkitMaskRepeat: 'no-repeat',
          maskRepeat: 'no-repeat',
        }}
      />

      {showGlow && (
        <div
          style={{
            position: 'absolute',
            top: glowTop,
            left: '50%',
            transform: 'translateX(-50%)',
            width: glowSize,
            height: glowSize * 0.4,
            background: `radial-gradient(ellipse, ${glowColor}18 0%, transparent 70%)`,
            pointerEvents: 'none',
          }}
        />
      )}
    </>
  );
};

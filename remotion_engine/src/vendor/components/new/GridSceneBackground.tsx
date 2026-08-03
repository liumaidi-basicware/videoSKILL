import React from 'react';
import {VerticalBackground, type VerticalBackgroundProps} from './VerticalBackground';

export interface GridSceneBackgroundProps
  extends Pick<
    VerticalBackgroundProps,
    | 'gridOpacity'
    | 'gridColor'
    | 'gridSize'
    | 'gridStrokeWidth'
    | 'gridBlur'
    | 'fadeTop'
    | 'fadeBottom'
  > {
  backdropStyle?: React.CSSProperties;
  accentGlowStyle?: React.CSSProperties;
  showAccentGlow?: boolean;
}

export const GridSceneBackground: React.FC<GridSceneBackgroundProps> = ({
  backdropStyle,
  accentGlowStyle,
  showAccentGlow = true,
  gridOpacity,
  gridColor,
  gridSize,
  gridStrokeWidth,
  gridBlur,
  fadeTop,
  fadeBottom,
}) => {
  return (
    <>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          pointerEvents: 'none',
          ...backdropStyle,
        }}
      />

      {showAccentGlow && accentGlowStyle ? (
        <div
          style={{
            position: 'absolute',
            pointerEvents: 'none',
            ...accentGlowStyle,
          }}
        />
      ) : null}

      <VerticalBackground
        showGlow={false}
        gridOpacity={gridOpacity}
        gridColor={gridColor}
        gridSize={gridSize}
        gridStrokeWidth={gridStrokeWidth}
        gridBlur={gridBlur}
        fadeTop={fadeTop}
        fadeBottom={fadeBottom}
      />
    </>
  );
};

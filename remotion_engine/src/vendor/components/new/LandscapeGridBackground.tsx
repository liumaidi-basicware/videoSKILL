import React from 'react';
import {GridSceneBackground, type GridSceneBackgroundProps} from './GridSceneBackground';

export type LandscapeGridBackgroundProps = GridSceneBackgroundProps;

export const LandscapeGridBackground: React.FC<LandscapeGridBackgroundProps> = ({
  backdropStyle,
  accentGlowStyle,
  showAccentGlow = true,
  gridOpacity = 0.18,
  gridSize = 64,
  gridStrokeWidth = 0.74,
  gridBlur = 0.28,
  fadeTop = 112,
  fadeBottom = 112,
  gridColor,
}) => {
  return (
    <GridSceneBackground
      showAccentGlow={showAccentGlow}
      gridOpacity={gridOpacity}
      gridColor={gridColor}
      gridSize={gridSize}
      gridStrokeWidth={gridStrokeWidth}
      gridBlur={gridBlur}
      fadeTop={fadeTop}
      fadeBottom={fadeBottom}
      backdropStyle={{
        background:
          'linear-gradient(180deg, rgb(8, 10, 14) 0%, rgb(3, 5, 8) 12%, rgb(0, 0, 0) 34%)',
        ...backdropStyle,
      }}
      accentGlowStyle={{
        top: -54,
        left: '50%',
        width: 1180,
        height: 300,
        transform: 'translateX(-50%)',
        opacity: 0.34,
        filter: 'blur(22px)',
        background:
          'radial-gradient(ellipse, rgba(0, 122, 255, 0.12) 0%, rgba(0, 122, 255, 0.05) 34%, rgba(0, 122, 255, 0.015) 56%, transparent 76%)',
        ...accentGlowStyle,
      }}
    />
  );
};

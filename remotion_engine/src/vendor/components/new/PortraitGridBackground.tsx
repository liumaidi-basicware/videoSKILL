import React from 'react';
import {GridSceneBackground, type GridSceneBackgroundProps} from './GridSceneBackground';

export type PortraitGridBackgroundProps = GridSceneBackgroundProps;

export const PortraitGridBackground: React.FC<PortraitGridBackgroundProps> = ({
  backdropStyle,
  accentGlowStyle,
  showAccentGlow = true,
  gridOpacity = 0.24,
  gridSize = 60,
  gridStrokeWidth = 0.78,
  gridBlur = 0.34,
  fadeTop = 156,
  fadeBottom = 156,
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
          'linear-gradient(180deg, rgb(8, 10, 14) 0%, rgb(3, 5, 8) 10%, rgb(0, 0, 0) 28%)',
        ...backdropStyle,
      }}
      accentGlowStyle={{
        top: -36,
        left: '50%',
        width: 760,
        height: 240,
        transform: 'translateX(-50%)',
        opacity: 0.42,
        filter: 'blur(18px)',
        background:
          'radial-gradient(ellipse, rgba(0, 122, 255, 0.14) 0%, rgba(0, 122, 255, 0.06) 30%, rgba(0, 122, 255, 0.02) 50%, transparent 74%)',
        ...accentGlowStyle,
      }}
    />
  );
};

import React from 'react';
import {
  LandscapeGridBackground,
  type LandscapeGridBackgroundProps,
} from './LandscapeGridBackground';

/**
 * Backward-compatible alias for the standardized 16:9 grid background.
 */
export const GridBackground: React.FC<LandscapeGridBackgroundProps> = (props) => {
  return <LandscapeGridBackground {...props} />;
};

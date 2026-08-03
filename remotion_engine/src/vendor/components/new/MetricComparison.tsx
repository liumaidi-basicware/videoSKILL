import React from 'react';
import { interpolate, spring } from 'remotion';
import { COLORS, FONTS, SIZES } from '../../design-system/tokens';

interface MetricItem {
  label: string;
  value: number;
  suffix: string;
  color: string;
}

interface MetricComparisonProps {
  title: string;
  before: MetricItem;
  after: MetricItem;
  frame: number;
  fps?: number;
  delay?: number;
}

export const MetricComparison: React.FC<MetricComparisonProps> = ({
  title,
  before,
  after,
  frame,
  fps = 30,
  delay = 0,
}) => {
  const f = Math.max(0, frame - delay);
  const titleOpacity = interpolate(f, [0, 12], [0, 1], { extrapolateLeft: 'clamp' });
  const barsSpring = spring({ frame: f - 12, fps, config: { damping: 14, stiffness: 60 } });

  const barMaxH = 240;
  const beforeH = (before.value / 100) * barMaxH * barsSpring;
  const afterH = (after.value / 100) * barMaxH * barsSpring;

  const beforeCount = interpolate(f, [15, 50], [0, before.value], { extrapolateLeft: 'clamp' });
  const afterCount = interpolate(f, [15, 50], [0, after.value], { extrapolateLeft: 'clamp' });

  return (
    <div style={{ width: '100%', maxWidth: 700, margin: '0 auto' }}>
      {/* Title */}
      <div style={{ opacity: titleOpacity, textAlign: 'center', marginBottom: 40 }}>
        <span style={{ fontSize: SIZES.h3, fontFamily: FONTS.display, fontWeight: 600, color: COLORS.text }}>
          {title}
        </span>
      </div>

      {/* Bars */}
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'flex-end', gap: 60, height: barMaxH + 60 }}>
        {/* Before */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
          <div style={{
            fontSize: SIZES.h2, fontFamily: FONTS.mono, fontWeight: 700,
            color: before.color,
          }}>
            {Math.floor(beforeCount)}{before.suffix}
          </div>
          <div style={{
            width: 60, height: beforeH,
            backgroundColor: before.color,
            borderRadius: 6, opacity: 0.7,
            transition: 'height 0.1s',
          }} />
          <span style={{ fontSize: SIZES.body, fontFamily: FONTS.text, color: COLORS.textSecondary, textAlign: 'center' }}>
            {before.label}
          </span>
        </div>

        {/* After */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
          <div style={{
            fontSize: SIZES.h2, fontFamily: FONTS.mono, fontWeight: 700,
            color: after.color,
          }}>
            {Math.floor(afterCount)}{after.suffix}
          </div>
          <div style={{
            width: 60, height: afterH,
            backgroundColor: after.color,
            borderRadius: 6, opacity: 0.9,
            transition: 'height 0.1s',
          }} />
          <span style={{ fontSize: SIZES.body, fontFamily: FONTS.text, color: COLORS.textSecondary, textAlign: 'center' }}>
            {after.label}
          </span>
        </div>
      </div>

      {/* Saving badge */}
      <div style={{
        textAlign: 'center', marginTop: 30,
        opacity: interpolate(f, [40, 60], [0, 1], { extrapolateLeft: 'clamp' }),
      }}>
        <span style={{
          fontSize: SIZES.body, fontFamily: FONTS.mono,
          color: COLORS.success, fontWeight: 600,
          padding: '8px 20px', borderRadius: 100,
          border: `1px solid ${COLORS.success}40`,
          backgroundColor: `${COLORS.success}10`,
        }}>
          -{before.value - after.value}{before.suffix}
        </span>
      </div>
    </div>
  );
};

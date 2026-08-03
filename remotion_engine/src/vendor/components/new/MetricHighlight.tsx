// MetricHighlight - 数据冲击片段组件
// 用于展示关键数据，红色强调 + 脉冲光效

import React from 'react';
import {
  useCurrentFrame,
  useVideoConfig,
  spring,
  interpolate,
  AbsoluteFill,
  Easing,
} from 'remotion';
import { COLORS, FONTS, SIZES } from '../../design-system/tokens';
import { SPRING_PRESETS } from '../../design-system/animations';

interface MetricHighlightProps {
  value: string;           // 主数值 "1.3秒" | "100万"
  highlight?: string;      // 副标题 "快2倍" | "≈29小时"
  accentColor?: string;    // 强调色，默认红色
  pulse?: boolean;         // 是否启用脉冲光效
  startDelay?: number;     // 动画延迟帧数
  showGlow?: boolean;      // 是否显示背景光晕
}

export const MetricHighlight: React.FC<MetricHighlightProps> = ({
  value,
  highlight,
  accentColor = COLORS.extended.red,
  pulse = true,
  startDelay = 0,
  showGlow = true,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const itemFrame = Math.max(0, frame - startDelay);

  // 数字弹性入场
  const scale = spring({
    frame: itemFrame,
    fps,
    config: SPRING_PRESETS.snappy,
  });

  // 数字从底部升起
  const y = interpolate(itemFrame, [0, 20], [60, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.back(1.5)),
  });

  // 透明度
  const opacity = interpolate(itemFrame, [0, 15], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // 脉冲光效（呼吸感）
  const pulseIntensity = pulse
    ? interpolate(
        (frame % 60) / 60,
        [0, 0.5, 1],
        [0.3, 0.6, 0.3],
        { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
      )
    : 0.4;

  // 光晕扩散
  const glowScale = interpolate(itemFrame, [0, 30], [0.5, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.quad),
  });

  // 副标题延迟入场
  const highlightFrame = Math.max(0, itemFrame - 15);
  const highlightOpacity = interpolate(highlightFrame, [0, 15], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const highlightY = interpolate(highlightFrame, [0, 15], [20, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // 底部装饰线宽度
  const lineWidth = interpolate(itemFrame, [20, 50], [0, 200], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.background,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      {/* 背景光晕 */}
      {showGlow && (
        <div
          style={{
            position: 'absolute',
            width: 600,
            height: 600,
            borderRadius: '50%',
            background: `radial-gradient(circle, ${accentColor}${Math.floor(pulseIntensity * 50).toString(16).padStart(2, '0')} 0%, transparent 70%)`,
            filter: 'blur(60px)',
            transform: `scale(${glowScale})`,
            pointerEvents: 'none',
          }}
        />
      )}

      {/* 主数值 */}
      <div
        style={{
          transform: `translateY(${y}px) scale(${0.85 + scale * 0.15})`,
          opacity,
        }}
      >
        <span
          style={{
            fontSize: SIZES.hero,
            fontFamily: FONTS.display,
            fontWeight: 800,
            color: accentColor,
            textShadow: `0 0 80px ${accentColor}60, 0 0 120px ${accentColor}30`,
            letterSpacing: '-2px',
          }}
        >
          {value}
        </span>
      </div>

      {/* 副标题 */}
      {highlight && (
        <div
          style={{
            marginTop: SIZES.spacing.lg,
            transform: `translateY(${highlightY}px)`,
            opacity: highlightOpacity,
          }}
        >
          <span
            style={{
              fontSize: SIZES.h3,
              fontFamily: FONTS.text,
              color: COLORS.text,
              fontWeight: 500,
              letterSpacing: '1px',
            }}
          >
            {highlight}
          </span>
        </div>
      )}

      {/* 底部装饰线 */}
      <div
        style={{
          position: 'absolute',
          bottom: '15%',
          width: lineWidth,
          height: 2,
          background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`,
          opacity: highlightOpacity * 0.5,
        }}
      />
    </AbsoluteFill>
  );
};
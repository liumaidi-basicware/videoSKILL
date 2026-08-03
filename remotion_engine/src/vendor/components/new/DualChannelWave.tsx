// DualChannelWave - 双通道声波组件
// 展示双通道独立工作、互不干扰

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

interface ChannelConfig {
  from: string;   // 源语言
  to: string;     // 目标语言
  color?: string; // 通道颜色
  label?: string; // 通道标签
}

interface DualChannelWaveProps {
  channelA: ChannelConfig;
  channelB: ChannelConfig;
  active?: boolean;
  startDelay?: number;
}

// 声波动画组件
const Waveform: React.FC<{
  color: string;
  frame: number;
  startX: number;
  width: number;
  y: number;
  active: boolean;
}> = ({ color, frame, startX, width, y, active }) => {
  // 生成声波路径
  const generateWavePath = () => {
    const points: string[] = [];
    const amplitude = 25;
    const frequency = 0.05;
    const speed = active ? frame * 0.1 : 0;

    for (let x = 0; x <= width; x += 2) {
      const waveY = Math.sin((x + speed * 10) * frequency) * amplitude;
      points.push(`${startX + x},${y + waveY}`);
    }
    return `M ${points.join(' L ')}`;
  };

  return (
    <path
      d={generateWavePath()}
      stroke={color}
      strokeWidth={3}
      fill="none"
      strokeLinecap="round"
      opacity={0.8}
    />
  );
};

export const DualChannelWave: React.FC<DualChannelWaveProps> = ({
  channelA,
  channelB,
  active = true,
  startDelay = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  const itemFrame = Math.max(0, frame - startDelay);

  // 通道A颜色（默认蓝色）
  const colorA = channelA.color || COLORS.primary;
  // 通道B颜色（默认紫色）
  const colorB = channelB.color || COLORS.extended.purple;

  // 整体入场
  const containerOpacity = interpolate(itemFrame, [0, 20], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // 通道A入场
  const channelAScale = spring({
    frame: itemFrame,
    fps,
    config: SPRING_PRESETS.smooth,
  });

  // 通道B入场（延迟）
  const channelBScale = spring({
    frame: itemFrame - 15,
    fps,
    config: SPRING_PRESETS.smooth,
  });

  // 分隔线入场
  const dividerWidth = interpolate(itemFrame - 20, [0, 20], [0, width * 0.6], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.quad),
  });

  // 标签入场
  const labelOpacity = interpolate(itemFrame - 30, [0, 15], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const waveWidth = 400;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.background,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        opacity: containerOpacity,
      }}
    >
      {/* 通道A */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: SIZES.spacing.lg,
          marginBottom: SIZES.spacing.xxl,
          transform: `scale(${channelAScale})`,
        }}
      >
        {/* 通道A标签 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: SIZES.spacing.sm,
            opacity: labelOpacity,
          }}
        >
          <span
            style={{
              fontSize: SIZES.body,
              fontFamily: FONTS.text,
              color: colorA,
              fontWeight: 600,
            }}
          >
            {channelA.label || '通道A'}
          </span>
        </div>

        {/* 声波SVG */}
        <svg width={waveWidth} height={60} style={{ overflow: 'visible' }}>
          <Waveform
            color={colorA}
            frame={frame}
            startX={0}
            width={waveWidth}
            y={30}
            active={active}
          />
        </svg>

        {/* 方向指示 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, opacity: labelOpacity }}>
          <span style={{ fontSize: SIZES.body, fontFamily: FONTS.text, color: COLORS.textSecondary }}>
            {channelA.from}
          </span>
          <span style={{ color: colorA, fontSize: SIZES.body }}>→</span>
          <span style={{ fontSize: SIZES.body, fontFamily: FONTS.text, color: COLORS.text }}>
            {channelA.to}
          </span>
        </div>
      </div>

      {/* 分隔线 */}
      <div
        style={{
          width: dividerWidth,
          height: 1,
          background: `linear-gradient(90deg, transparent, ${COLORS.textTertiary}, transparent)`,
          marginBottom: SIZES.spacing.xxl,
        }}
      />

      {/* 通道B */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: SIZES.spacing.lg,
          transform: `scale(${channelBScale})`,
        }}
      >
        {/* 通道B标签 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: SIZES.spacing.sm,
            opacity: labelOpacity,
          }}
        >
          <span
            style={{
              fontSize: SIZES.body,
              fontFamily: FONTS.text,
              color: colorB,
              fontWeight: 600,
            }}
          >
            {channelB.label || '通道B'}
          </span>
        </div>

        {/* 声波SVG */}
        <svg width={waveWidth} height={60} style={{ overflow: 'visible' }}>
          <Waveform
            color={colorB}
            frame={frame}
            startX={0}
            width={waveWidth}
            y={30}
            active={active}
          />
        </svg>

        {/* 方向指示 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, opacity: labelOpacity }}>
          <span style={{ fontSize: SIZES.body, fontFamily: FONTS.text, color: COLORS.textSecondary }}>
            {channelB.from}
          </span>
          <span style={{ color: colorB, fontSize: SIZES.body }}>→</span>
          <span style={{ fontSize: SIZES.body, fontFamily: FONTS.text, color: COLORS.text }}>
            {channelB.to}
          </span>
        </div>
      </div>

      {/* 底部说明 */}
      <div
        style={{
          position: 'absolute',
          bottom: '15%',
          opacity: interpolate(itemFrame - 50, [0, 20], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          }),
        }}
      >
        <span
          style={{
            fontSize: SIZES.h4,
            fontFamily: FONTS.text,
            color: COLORS.textSecondary,
          }}
        >
          双通道独立输出，互不干扰
        </span>
      </div>
    </AbsoluteFill>
  );
};
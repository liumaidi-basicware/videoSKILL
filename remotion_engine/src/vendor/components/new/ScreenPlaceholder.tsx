import React from 'react';
import { interpolate, spring } from 'remotion';
import { COLORS, FONTS, SIZES } from '../../design-system/tokens';

interface ScreenPlaceholderProps {
  /** Current frame from useCurrentFrame() */
  frame: number;
  /** Frame delay before animation starts */
  delay?: number;
  /** Label text shown in the placeholder */
  label?: string;
  /** Custom height (default: auto from aspectRatio) */
  height?: number;
  /** Aspect ratio for landscape recordings placed inside (default: 16/9) */
  aspectRatio?: number;
  /** Max width override */
  maxWidth?: number;
  /** Horizontal padding override */
  width?: string;
  /** FPS for spring calculation */
  fps?: number;
}

/**
 * 录屏占位组件
 * 用于标记视频中需要用户后期贴上录屏素材的区域
 * 显示为虚线边框 + 居中图标 + 标签文字
 *
 * 用法:
 *   <ScreenPlaceholder frame={frame} delay={20} label="操作演示" />
 */
export const ScreenPlaceholder: React.FC<ScreenPlaceholderProps> = ({
  frame,
  delay = 0,
  label = '录制画面',
  height,
  aspectRatio = 16 / 9,
  maxWidth = 800,
  width = '85%',
  fps = 30,
}) => {
  const f = Math.max(0, frame - delay);
  const opacity = interpolate(f, [0, 12], [0, 1], { extrapolateLeft: 'clamp' });
  const s = spring({ frame: f, fps, config: { damping: 16, stiffness: 70 } });

  return (
    <div
      style={{
        width,
        maxWidth,
        height: height ?? 'auto',
        aspectRatio: height ? undefined : aspectRatio,
        borderRadius: 12,
        border: `2px dashed ${COLORS.textTertiary}50`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        opacity,
        transform: `scale(${0.92 + s * 0.08})`,
        margin: '16px auto',
      }}
    >
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 36, color: COLORS.textTertiary, marginBottom: 8 }}>⊞</div>
        <span style={{ fontSize: SIZES.body, fontFamily: FONTS.text, color: COLORS.textTertiary }}>
          {label}
        </span>
      </div>
    </div>
  );
};

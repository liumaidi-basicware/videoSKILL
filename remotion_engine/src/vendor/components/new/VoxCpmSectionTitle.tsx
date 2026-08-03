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

interface VoxCpmSectionTitleProps {
  title: string;
  subtitle?: string;
  accentColor: string;
  variant: 'slideLeft' | 'slideUp' | 'scaleIn' | 'centerExpand';
  decorative?: 'grid' | 'dots' | 'wave' | 'none';
  cornerTag?: string;
}

export const VoxCpmSectionTitle: React.FC<VoxCpmSectionTitleProps> = ({
  title,
  subtitle,
  accentColor,
  variant = 'slideLeft',
  decorative = 'none',
  cornerTag,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // --- 背景装饰 ---
  const renderDecorative = () => {
    switch (decorative) {
      case 'grid':
        return (
          <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.05 }}>
            <defs>
              <pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
                <path d="M 60 0 L 0 0 0 60" fill="none" stroke={COLORS.text} strokeWidth="0.5" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#grid)" />
          </svg>
        );
      case 'dots':
        return (
          <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.08 }}>
            <defs>
              <pattern id="dots" width="40" height="40" patternUnits="userSpaceOnUse">
                <circle cx="20" cy="20" r="1.5" fill={COLORS.text} />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#dots)" />
          </svg>
        );
      case 'wave':
        return (
          <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: '40%', opacity: 0.06 }}>
            <svg viewBox="0 0 1920 400" preserveAspectRatio="none" style={{ width: '100%', height: '100%' }}>
              <path
                d={`M 0 200 Q ${240 + Math.sin(frame * 0.02) * 60} 100, 480 200 T 960 200 T 1440 200 T 1920 200 L 1920 400 L 0 400 Z`}
                fill={accentColor}
              />
            </svg>
          </div>
        );
      default:
        return null;
    }
  };

  // --- 标题动画 4 种变体 ---
  const getTitleStyle = (): React.CSSProperties => {
    const base: React.CSSProperties = {
      fontSize: SIZES.h1,
      fontWeight: 700,
      fontFamily: FONTS.display,
      color: COLORS.text,
      letterSpacing: '-1px',
      lineHeight: 1.2,
      margin: 0,
    };

    switch (variant) {
      case 'slideLeft': {
        const progress = spring({ frame: frame - 5, fps, config: SPRING_PRESETS.snappy });
        return {
          ...base,
          opacity: interpolate(frame - 5, [0, 15], [0, 1], { extrapolateLeft: 'clamp' }),
          transform: `translateX(${(1 - progress) * -8}px)`,
        };
      }
      case 'slideUp': {
        const progress = spring({ frame: frame - 5, fps, config: SPRING_PRESETS.snappy });
        return {
          ...base,
          opacity: interpolate(frame - 5, [0, 15], [0, 1], { extrapolateLeft: 'clamp' }),
          transform: `translateY(${(1 - progress) * 8}px)`,
        };
      }
      case 'scaleIn': {
        const progress = spring({ frame: frame - 5, fps, config: { damping: 20, stiffness: 80 } });
        return {
          ...base,
          opacity: interpolate(frame - 5, [0, 15], [0, 1], { extrapolateLeft: 'clamp' }),
          transform: `scale(${0.95 + progress * 0.05})`,
        };
      }
      case 'centerExpand': {
        const progress = spring({ frame: frame - 5, fps, config: { damping: 16, stiffness: 60 } });
        const clipAmount = interpolate(frame - 5, [0, 22], [50, 0], {
          extrapolateLeft: 'clamp',
          easing: Easing.out(Easing.cubic),
        });
        return {
          ...base,
          opacity: interpolate(frame - 5, [0, 10], [0, 1], { extrapolateLeft: 'clamp' }),
          clipPath: `inset(0 ${clipAmount}% 0 ${clipAmount}%)`,
          transform: `scale(${0.95 + progress * 0.05})`,
        };
      }
    }
  };

  // --- 副标题动画 ---
  const subtitleOpacity = interpolate(frame, [15, 30], [0, 1], { extrapolateLeft: 'clamp' });
  // 副标题完全显示后微弱的呼吸脉动
  const subtitleBreathe = interpolate(
    Math.sin(frame * 0.04),
    [-1, 1],
    [0.92, 1],
  );

  // --- 左侧装饰条（仅 slideLeft 变体） ---
  const renderLeftBar = () => {
    if (variant !== 'slideLeft') return null;
    const barScale = spring({ frame: frame, fps, config: SPRING_PRESETS.snappy });
    return (
      <div
        style={{
          position: 'absolute',
          left: 120,
          top: '50%',
          width: 4,
          height: 100,
          backgroundColor: accentColor,
          borderRadius: 2,
          transform: `translateY(-50%) scaleY(${barScale})`,
        }}
      />
    );
  };

  // --- 右上角标签 ---
  const cornerOpacity = interpolate(frame, [10, 25], [0, 1], { extrapolateLeft: 'clamp' });

  // --- 底部进度条 ---
  const barOpacity = interpolate(frame, [20, 35], [0, 1], { extrapolateLeft: 'clamp' });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.background, overflow: 'hidden' }}>
      {renderDecorative()}
      {renderLeftBar()}

      {/* 背景大字 */}
      <div
        style={{
          position: 'absolute',
          right: -40,
          bottom: -40,
          fontSize: 500,
          fontWeight: 700,
          fontFamily: FONTS.display,
          color: `${accentColor}08`,
          lineHeight: 1,
          userSelect: 'none',
          pointerEvents: 'none',
        }}
      >
        {title.charAt(0)}
      </div>

      {/* 右上角标签 */}
      {cornerTag && (
        <div
          style={{
            position: 'absolute',
            top: 60,
            right: 80,
            padding: '10px 24px',
            borderRadius: SIZES.radius.xl,
            border: `1px solid ${accentColor}40`,
            backgroundColor: `${accentColor}10`,
            opacity: cornerOpacity,
          }}
        >
          <span style={{ fontSize: SIZES.body, color: accentColor, fontFamily: FONTS.mono, fontWeight: 500 }}>
            {cornerTag}
          </span>
        </div>
      )}

      {/* 主内容 */}
      <div
        style={{
          position: 'absolute',
          left: variant === 'slideLeft' ? 180 : 120,
          top: '50%',
          transform: 'translateY(-50%)',
          maxWidth: 900,
        }}
      >
        <h2 style={getTitleStyle()}>{title}</h2>
        {subtitle && (
          <p
            style={{
              fontSize: SIZES.h3,
              fontFamily: FONTS.text,
              color: COLORS.textSecondary,
              marginTop: SIZES.spacing.lg,
              opacity: subtitleOpacity * subtitleBreathe,
              lineHeight: 1.5,
            }}
          >
            {subtitle}
          </p>
        )}
      </div>

      {/* 底部进度 */}
      <div
        style={{
          position: 'absolute',
          bottom: 60,
          left: 120,
          right: 120,
          height: 2,
          backgroundColor: COLORS.backgroundElevated,
          borderRadius: 1,
          opacity: barOpacity,
        }}
      >
        <div
          style={{
            width: '30%',
            height: '100%',
            backgroundColor: accentColor,
            borderRadius: 1,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

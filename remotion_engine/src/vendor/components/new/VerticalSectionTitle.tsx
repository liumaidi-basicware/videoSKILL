import React from 'react';
import { useCurrentFrame, spring, interpolate } from 'remotion';
import { COLORS, FONTS, SIZES } from '../../design-system/tokens';
import { SPRING_PRESETS } from '../../design-system/animations';
import { VERTICAL, VERTICAL_FPS } from './vertical';

interface VerticalSectionTitleProps {
  /** 标题文字 */
  title: string;
  /** 触发帧延迟 */
  delay?: number;
  /** 副标题 */
  subtitle?: string;
  /** 强调色 */
  accentColor?: string;
  /**
   * 入场动画变体
   * - scale: 缩放入场（默认）
   * - fade: 仅淡入（极简）
   */
  variant?: 'scale' | 'fade';
}

/**
 * 竖屏章节标题组件
 * 自动应用竖屏安全区和排版字号
 *
 * 用法:
 *   <VerticalSectionTitle
 *     title="核心功能"
 *     subtitle="一次调用查全局"
 *     delay={10}
 *   />
 */
export const VerticalSectionTitle: React.FC<VerticalSectionTitleProps> = ({
  title,
  delay = 0,
  subtitle,
  accentColor = COLORS.text,
  variant = 'scale',
}) => {
  const frame = useCurrentFrame();
  const f = Math.max(0, frame - delay);

  const titleOpacity = interpolate(f, [0, 15], [0, 1], { extrapolateLeft: 'clamp' });

  const titleSpring = variant === 'scale'
    ? spring({ frame: f, fps: VERTICAL_FPS, config: SPRING_PRESETS.snappy })
    : 1;

  const titleTransform = variant === 'scale'
    ? `scale(${0.95 + titleSpring * 0.05})`
    : undefined;

  const subOpacity = interpolate(f, [15, 30], [0, 1], { extrapolateLeft: 'clamp' });

  return (
    <div style={{ textAlign: 'center', marginBottom: 20 }}>
      <h2
        style={{
          fontSize: 48,
          fontFamily: FONTS.display,
          fontWeight: 700,
          color: accentColor,
          letterSpacing: '-1px',
          margin: 0,
          opacity: titleOpacity,
          transform: titleTransform,
          maxWidth: VERTICAL.CONTENT_MAX_WIDTH,
        }}
      >
        {title}
      </h2>
      {subtitle && (
        <p
          style={{
            fontSize: SIZES.body,
            fontFamily: FONTS.text,
            color: COLORS.textSecondary,
            margin: '8px 0 0',
            opacity: subOpacity,
            lineHeight: 1.5,
          }}
        >
          {subtitle}
        </p>
      )}
    </div>
  );
};

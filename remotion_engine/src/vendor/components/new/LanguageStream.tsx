// LanguageStream - 多语言流动组件
// 展示支持的语言列表，水平流动入场

import React from 'react';
import {
  useCurrentFrame,
  spring,
  interpolate,
  AbsoluteFill,
  Easing,
} from 'remotion';
import { COLORS, FONTS, SIZES } from '../../design-system/tokens';
import { SPRING_PRESETS } from '../../design-system/animations';

interface LanguageStreamProps {
  languages: string[];
  moreText?: string;
  startDelay?: number;
}

// 单个语言标签
const LanguageTag: React.FC<{
  language: string;
  index: number;
  frame: number;
  startDelay: number;
}> = ({ language, index, frame, startDelay }) => {
  const itemDelay = startDelay + index * 5; // 每个标签间隔5帧
  const itemFrame = Math.max(0, frame - itemDelay);

  // 弹性入场
  const scale = spring({
    frame: itemFrame,
    fps: 30,
    config: SPRING_PRESETS.snappy,
  });

  // 从左侧滑入
  const x = interpolate(itemFrame, [0, 15], [-30, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const opacity = interpolate(itemFrame, [0, 10], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // 交替颜色
  const colors = [
    COLORS.primary,
    COLORS.extended.purple,
    COLORS.extended.cyan,
    COLORS.extended.orange,
    COLORS.extended.green,
    COLORS.extended.pink,
    COLORS.extended.indigo,
    COLORS.extended.yellow,
  ];
  const tagColor = colors[index % colors.length];

  return (
    <div
      style={{
        padding: `${SIZES.spacing.sm}px ${SIZES.spacing.lg}px`,
        backgroundColor: `${tagColor}15`,
        borderRadius: SIZES.radius.xl,
        border: `1px solid ${tagColor}40`,
        transform: `translateX(${x}px) scale(${0.9 + scale * 0.1})`,
        opacity,
        display: 'flex',
        alignItems: 'center',
        gap: SIZES.spacing.xs,
      }}
    >
      {/* 语言文字 */}
      <span
        style={{
          fontSize: SIZES.body,
          fontFamily: FONTS.text,
          color: COLORS.text,
          fontWeight: 500,
        }}
      >
        {language}
      </span>
    </div>
  );
};

export const LanguageStream: React.FC<LanguageStreamProps> = ({
  languages,
  moreText = '更多语言持续增加...',
  startDelay = 0,
}) => {
  const frame = useCurrentFrame();

  // 容器入场
  const containerOpacity = interpolate(frame - startDelay, [0, 20], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // 分成两行，每行4个
  const row1 = languages.slice(0, 4);
  const row2 = languages.slice(4, 8);

  // 更多文字入场
  const moreOpacity = interpolate(frame - startDelay - languages.length * 5 - 20, [0, 15], [0, 1], {
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
        gap: SIZES.spacing.lg,
        opacity: containerOpacity,
      }}
    >
      {/* 标题 */}
      <div
        style={{
          marginBottom: SIZES.spacing.xl,
        }}
      >
        <span
          style={{
            fontSize: SIZES.h3,
            fontFamily: FONTS.display,
            color: COLORS.textSecondary,
            letterSpacing: '2px',
          }}
        >
          支持 8+ 种语言
        </span>
      </div>

      {/* 第一行 */}
      <div
        style={{
          display: 'flex',
          gap: SIZES.spacing.md,
          flexWrap: 'wrap',
          justifyContent: 'center',
        }}
      >
        {row1.map((lang, index) => (
          <LanguageTag
            key={index}
            language={lang}
            index={index}
            frame={frame}
            startDelay={startDelay + 20}
          />
        ))}
      </div>

      {/* 第二行 */}
      <div
        style={{
          display: 'flex',
          gap: SIZES.spacing.md,
          flexWrap: 'wrap',
          justifyContent: 'center',
        }}
      >
        {row2.map((lang, index) => (
          <LanguageTag
            key={index + 4}
            language={lang}
            index={index + 4}
            frame={frame}
            startDelay={startDelay + 20}
          />
        ))}
      </div>

      {/* 更多语言提示 */}
      <div
        style={{
          marginTop: SIZES.spacing.xl,
          opacity: moreOpacity,
        }}
      >
        <span
          style={{
            fontSize: SIZES.body,
            fontFamily: FONTS.text,
            color: COLORS.textTertiary,
            fontStyle: 'italic',
          }}
        >
          {moreText}
        </span>
      </div>
    </AbsoluteFill>
  );
};
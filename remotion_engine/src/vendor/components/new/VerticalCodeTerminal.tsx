import React from 'react';
import { interpolate } from 'remotion';
import { COLORS, FONTS, SIZES } from '../../design-system/tokens';

interface VerticalCodeTerminalProps {
  lines: string[];
  frame: number;
  fps?: number;
  typingSpeed?: number; // chars per frame
  delay?: number;
  prompt?: string;
  filename?: string;
}

export const VerticalCodeTerminal: React.FC<VerticalCodeTerminalProps> = ({
  lines,
  frame,
  typingSpeed = 3,
  delay = 0,
  prompt = '$',
  filename,
}) => {
  const f = Math.max(0, frame - delay);
  const opacity = interpolate(f, [0, 10], [0, 1], { extrapolateLeft: 'clamp' });

  // Calculate total chars for typing effect
  const fullText = lines.join('\n');
  const totalChars = Math.floor(f * typingSpeed);
  const displayedChars = Math.min(totalChars, fullText.length);

  // Build displayed lines
  let remaining = displayedChars;
  const displayLines = lines.map((line) => {
    if (remaining <= 0) return '';
    if (remaining >= line.length) {
      remaining -= line.length;
      return line;
    }
    const partial = line.slice(0, remaining);
    remaining = 0;
    return partial;
  });

  // Cursor blink
  const cursorVisible = f % 10 < 6;

  return (
    <div style={{
      width: '100%', maxWidth: 700,
      borderRadius: SIZES.radius.lg,
      border: `1px solid ${COLORS.backgroundSecondary}`,
      backgroundColor: `${COLORS.backgroundElevated}80`,
      overflow: 'hidden',
      opacity,
      margin: '0 auto',
    }}>
      {/* Title bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '10px 16px',
        backgroundColor: `${COLORS.backgroundSecondary}60`,
        borderBottom: `1px solid ${COLORS.backgroundSecondary}`,
      }}>
        <div style={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: '#FF5F56' }} />
        <div style={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: '#FFBD2E' }} />
        <div style={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: '#27C93F' }} />
        {filename && (
          <span style={{
            marginLeft: 12, fontSize: SIZES.caption,
            fontFamily: FONTS.mono, color: COLORS.textTertiary,
          }}>
            {filename}
          </span>
        )}
      </div>

      {/* Code content */}
      <div style={{ padding: '16px 20px', fontFamily: FONTS.mono, fontSize: SIZES.body, lineHeight: 1.8 }}>
        {lines.map((line, i) => {
          const isLast = i === lines.length - 1;
          const displayText = displayLines[i];
          if (!displayText && isLast && remaining <= 0) {
            return null;
          }
          return (
            <div key={i} style={{ display: 'flex', gap: 8 }}>
              {i === 0 && (
                <span style={{ color: COLORS.success, userSelect: 'none' }}>{prompt}</span>
              )}
              {i > 0 && (
                <span style={{ color: COLORS.textTertiary, userSelect: 'none', width: 16 }}>
                  {i + 1}
                </span>
              )}
              <span style={{
                color: i === 0 ? COLORS.text : COLORS.textSecondary,
                wordBreak: 'break-all',
              }}>
                {displayText || (isLast ? '' : ' ')}
              </span>
              {isLast && (
                <span style={{
                  display: cursorVisible ? 'inline' : 'none',
                  color: COLORS.primary,
                  fontWeight: 700,
                }}>
                  ▊
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

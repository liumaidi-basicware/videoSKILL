import React from 'react';

/**
 * 竖屏安全区包装组件
 * 确保内容在手机屏幕 notch、home indicator 区域内可见
 *
 * 竖屏画布: 1080×1920
 *   顶部安全区: 100px (避开 notch)
 *   底部安全区: 100px (避开 home indicator)
 *   内容区: 880px 高
 *   左右安全边距: 60px
 *
 * 用法:
 *   <SafeArea style={{ gap: 16 }}>
 *     <YourContent />
 *   </SafeArea>
 */
export const SafeArea: React.FC<{
  children: React.ReactNode;
  style?: React.CSSProperties;
  top?: number;
  bottom?: number;
  side?: number;
}> = ({ children, style, top = 100, bottom = 100, side = 60 }) => (
  <div
    style={{
      position: 'absolute',
      inset: 0,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: `${top}px ${side}px ${bottom}px`,
      ...style,
    }}
  >
    {children}
  </div>
);

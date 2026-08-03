/**
 * 竖屏（9:16）视频组件常量与工具
 *
 * 画布: 1080×1920
 * 所有竖屏组件共享同一套布局规则。
 *
 * 使用方式:
 *   import { VERTICAL } from '../../components/new/vertical';
 *   <SafeArea style={{ gap: VERTICAL.GAP }}>
 */

export const VERTICAL = {
  /** 画布宽 */
  WIDTH: 1080,
  /** 画布高 */
  HEIGHT: 1920,
  /** 顶部安全区（避开 notch） */
  SAFE_TOP: 100,
  /** 底部安全区（避开 home indicator） */
  SAFE_BOTTOM: 100,
  /** 左右安全边距 */
  SAFE_SIDE: 60,
  /** 内容最大宽度 */
  CONTENT_MAX_WIDTH: 800,
  /** 内容宽度百分比 */
  CONTENT_WIDTH: '90%',
  /** 段落内元素间距 */
  GAP: 16,
  /** 段间距 */
  SECTION_GAP: 30,
} as const;

/** FPS */
export const VERTICAL_FPS = 30;

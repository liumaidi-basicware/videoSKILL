/**
 * 竖屏段落模板（注释说明，不作为实际组件使用）
 *
 * 创建新竖屏段落时参考以下模式:
 *
 * import { useCurrentFrame, spring, interpolate, AbsoluteFill } from 'remotion';
 * import { COLORS } from '../../design-system/tokens';
 * import { SPRING_PRESETS } from '../../design-system/animations';
 * import { PortraitGridBackground, SafeArea, VerticalSectionTitle, VERTICAL } from '../../components/new';
 *
 * const Segment: React.FC = () => {
 *   const frame = useCurrentFrame();
 *   // spring entrance
 *   const s = spring({ frame: frame - 10, fps: 30, config: SPRING_PRESETS.snappy });
 *   // fade
 *   const op = interpolate(frame, [0, 15], [0, 1], { extrapolateLeft: 'clamp' });
 *   return (
 *     <AbsoluteFill style={{ backgroundColor: COLORS.background }}>
 *       <PortraitGridBackground />
 *       <SafeArea style={{ gap: VERTICAL.GAP }}>
 *         <VerticalSectionTitle title="标题" delay={0} />
 *         {/ * content here * /}
 *       </SafeArea>
 *     </AbsoluteFill>
 *   );
 * };
 */

export {};

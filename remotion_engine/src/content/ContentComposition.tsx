import React from "react";
import { AbsoluteFill, Series } from "remotion";
import { ContentSpec } from "./contentTypes";
import { SceneRenderer } from "./SceneRenderer";
import {
  PortraitGridBackground,
  LandscapeGridBackground,
  FadeTransition,
  SlideTransition,
  LightSweep,
  ZoomBlurTransition,
  CurtainReveal,
} from "../vendor/components/new";

// 文档/PPT 型内容动效成片：网格背景层 + 组件场景序列 + 转场。
// remotion-com-skills 组件库集成入口。竖屏/横屏各用对应网格背景（组件库强制约定）。
export const ContentComposition: React.FC<ContentSpec> = (props) => {
  const scenes = props.scenes || [];
  const isPortrait =
    props.orientation === "portrait" || props.height >= props.width;
  const Bg = isPortrait ? PortraitGridBackground : LandscapeGridBackground;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {/* 全片共享的网格背景层，位于所有 Sequence 之前 */}
      <Bg />
      <Series>
        {scenes.map((scene, i) => (
          <Series.Sequence
            key={i}
            durationInFrames={Math.max(1, scene.durationInFrames)}
          >
            <AbsoluteFill>
              <SceneRenderer scene={scene} />
              <TransitionLayer
                kind={scene.transition}
                height={props.height}
              />
            </AbsoluteFill>
          </Series.Sequence>
        ))}
      </Series>
    </AbsoluteFill>
  );
};

const TransitionLayer: React.FC<{
  kind?: string;
  height: number;
}> = ({ kind, height }) => {
  switch (kind) {
    case "fade":
      return <FadeTransition>{null}</FadeTransition>;
    case "slide":
      return <SlideTransition>{null}</SlideTransition>;
    case "lightsweep":
      return <LightSweep height={height} />;
    case "zoomblur":
      return <ZoomBlurTransition>{null}</ZoomBlurTransition>;
    case "curtain":
      return <CurtainReveal>{null}</CurtainReveal>;
    default:
      return null;
  }
};

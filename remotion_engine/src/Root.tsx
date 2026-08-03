import React from "react";
import { Composition } from "remotion";
import { ShotSequence } from "./ShotSequence";
import { ShotList } from "./types";
import { ContentComposition } from "./content/ContentComposition";
import { ContentSpec } from "./content/contentTypes";
import { HorizontalKinetic } from "./HorizontalKinetic";
import { HorizontalKineticProps } from "./types";

// 默认 props（无 --props 时也能预览）。真实渲染由 hf/remotion 引擎注入 shotlist JSON。
const defaultProps: ShotList = {
  width: 1080, height: 1920, fps: 30, brandPrimary: "#E60012",
  shots: [
    { durationInFrames: 60, move: "ken_burns", bg: "linear-gradient(135deg,#1e3a8a,#0ea5e9)",
      title: "示例内容页", bullets: ["运镜由 Remotion 完成", "字幕由 HyperFrames 叠加"], humanSlot: "right" },
  ],
};

// 内容动效默认 props（文档/PPT 型，remotion-com-skills 组件库驱动）。
const defaultContentProps: ContentSpec = {
  width: 1080, height: 1920, fps: 30, brandPrimary: "#007AFF", orientation: "portrait",
  scenes: [
    { kind: "hero", durationInFrames: 90, transition: "fade",
      props: { title: "示例标题", subtitle: "内容动效引擎", tags: ["文档转视频", "组件库驱动"] } },
    { kind: "section", durationInFrames: 60,
      props: { sectionNumber: 1, title: "章节示例", progress: 0.3 } },
  ],
};

const defaultKineticProps: HorizontalKineticProps = {
  videoPath: "input/sample-speaker.mp4", durationInSeconds: 9, title: "横版口播", eyebrow: "KINETIC TALK", palette: "green",
  captions: [{start: 0, end: 3, text: "先把一个真实场景讲清楚"}],
  scenes: [{start: 0, end: 9, layout: "intro", accent: "green", kicker: "开场", title: "先讲清楚\n再做复杂", subtitle: "口播、字幕和动态卡片由同一条时间轴驱动", items: [{at: 0, label: "01", title: "真实场景", detail: "让观众第一秒知道视频解决什么问题"}], pip: true}],
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Shots"
        component={ShotSequence as React.FC<Record<string, unknown>>}
        durationInFrames={60}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={defaultProps as unknown as Record<string, unknown>}
        calculateMetadata={({ props }) => {
          const p = props as unknown as ShotList;
          const total = (p.shots || []).reduce((s, sh) => s + Math.max(1, sh.durationInFrames), 0) || 60;
          return { durationInFrames: total, fps: p.fps || 30, width: p.width || 1080, height: p.height || 1920 };
        }}
      />
      <Composition
        id="HorizontalKinetic"
        component={HorizontalKinetic as React.FC<Record<string, unknown>>}
        durationInFrames={270}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={defaultKineticProps as unknown as Record<string, unknown>}
        calculateMetadata={({ props }) => {
          const p = props as unknown as HorizontalKineticProps;
          return {durationInFrames: Math.max(1, Math.round((p.durationInSeconds || 1) * (p.scenes?.length ? 30 : 30))), fps: 30, width: 1920, height: 1080};
        }}
      />
      <Composition
        id="Content"
        component={ContentComposition as React.FC<Record<string, unknown>>}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={defaultContentProps as unknown as Record<string, unknown>}
        calculateMetadata={({ props }) => {
          const p = props as unknown as ContentSpec;
          const total = (p.scenes || []).reduce((s, sc) => s + Math.max(1, sc.durationInFrames), 0) || 150;
          return { durationInFrames: total, fps: p.fps || 30, width: p.width || 1080, height: p.height || 1920 };
        }}
      />
    </>
  );
};

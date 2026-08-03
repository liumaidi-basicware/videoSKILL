import React from "react";
import { AbsoluteFill, Series, interpolate, useCurrentFrame } from "remotion";
import { ShotList, DEFAULT_FONT } from "./types";
import { ShotView } from "./Shot";
import { SubtitleTrack } from "./SubtitleTrack";

const TRANSITION_FRAMES = 8;

const TransitionedShot: React.FC<{
  shot: ShotList["shots"][number];
  previous?: ShotList["shots"][number];
  brand: string;
  font: string;
}> = ({shot, previous, brand, font}) => {
  const frame = useCurrentFrame();
  const transition = previous && shot.transition !== "cut" ? shot.transition : "cut";
  const overlap = Math.min(TRANSITION_FRAMES, shot.durationInFrames, previous?.durationInFrames || 0);
  const progress = overlap > 0
    ? interpolate(frame, [0, overlap], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"})
    : 1;
  const previousTail = previous ? {
    ...previous,
    sourceStartFrame: (previous.sourceStartFrame || 0) + previous.durationInFrames - overlap,
    durationInFrames: overlap,
  } : undefined;

  return <AbsoluteFill>
    {transition !== "cut" && previousTail && frame < overlap && (
      <ShotView shot={previousTail} brand={brand} font={font} muted showContent={false} />
    )}
    <AbsoluteFill style={transition === "fade"
      ? {opacity: progress}
      : transition === "slide" ? {transform: `translateX(${(1 - progress) * 100}%)`} : undefined}>
      <ShotView shot={shot} brand={brand} font={font} />
    </AbsoluteFill>
  </AbsoluteFill>;
};

// Shots retain their full timeline duration. Fade/slide overlap the previous visual tail
// under the incoming shot, while only the incoming source carries audio.
// 字幕轨叠在整个序列之上：用全局绝对帧定位（跟配音走，独立于镜头切换）。
export const ShotSequence: React.FC<ShotList> = (props) => {
  const brand = props.brandPrimary || "#E60012";
  const font = props.fontFamily || DEFAULT_FONT;
  const shots = props.shots || [];
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <Series>
        {shots.map((shot, i) => (
          <Series.Sequence key={i} durationInFrames={Math.max(1, shot.durationInFrames)}>
            <TransitionedShot shot={shot} previous={i > 0 ? shots[i - 1] : undefined}
              brand={brand} font={font} />
          </Series.Sequence>
        ))}
      </Series>
      {/* 字幕轨：覆盖整条成片顶层，绝对帧从合成起点计（与 final_edit 编译一致） */}
      <SubtitleTrack subtitles={props.subtitles} font={font} />
    </AbsoluteFill>
  );
};

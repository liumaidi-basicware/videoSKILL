import React from "react";
import { AbsoluteFill, Sequence, interpolate, useCurrentFrame } from "remotion";
import { SubtitleCue, DEFAULT_FONT } from "./types";

// 单条字幕：底部居中、半透明底衬、快速淡入淡出。真实字体渲染，中文/粤语不乱码。
const Cue: React.FC<{ text: string; durationInFrames: number; font: string }> = ({
  text, durationInFrames, font,
}) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, 5], [0, 1], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(
    frame,
    [Math.max(0, durationInFrames - 5), durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const op = Math.min(fadeIn, fadeOut);
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center",
      paddingBottom: 120, fontFamily: font || DEFAULT_FONT }}>
      <div style={{ opacity: op, maxWidth: "88%", textAlign: "center",
        background: "rgba(0,0,0,0.5)", color: "#fff", fontSize: 46, fontWeight: 700,
        lineHeight: 1.35, padding: "14px 32px", borderRadius: 10,
        textShadow: "0 2px 8px rgba(0,0,0,0.8)" }}>
        {text}
      </div>
    </AbsoluteFill>
  );
};

// 字幕轨：一组按绝对帧定位的字幕（跟配音走，独立于镜头切换）。
export const SubtitleTrack: React.FC<{ subtitles?: SubtitleCue[]; font: string }> = ({
  subtitles, font,
}) => {
  if (!subtitles || subtitles.length === 0) return null;
  return (
    <AbsoluteFill>
      {subtitles.map((c, i) => (
        <Sequence key={i} from={Math.max(0, c.fromFrame)}
          durationInFrames={Math.max(1, c.durationInFrames)}>
          <Cue text={c.text} durationInFrames={Math.max(1, c.durationInFrames)} font={font} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};

import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { Shot, DEFAULT_FONT } from "./types";

// PPT 内容页：标题 + 要点列表，逐条淡入。数字人槽位留白（fuse 阶段叠人）。
export const ContentPage: React.FC<{ shot: Shot; brand: string; font: string }> = ({
  shot, brand, font,
}) => {
  const frame = useCurrentFrame();
  const contentPad = shot.humanSlot === "left" ? { paddingLeft: "42%" }
    : shot.humanSlot === "right" ? { paddingRight: "42%" }
    : {};
  return (
    <AbsoluteFill
      style={{ padding: 90, boxSizing: "border-box", justifyContent: "center",
        fontFamily: font || DEFAULT_FONT, ...contentPad }}
    >
      {shot.title && (
        <div style={{ fontSize: 84, fontWeight: 800, color: "#fff", marginBottom: 40,
          textShadow: "0 4px 24px rgba(0,0,0,0.45)",
          borderLeft: `12px solid ${brand}`, paddingLeft: 28 }}>
          {shot.title}
        </div>
      )}
      {(shot.bullets || []).map((b, i) => {
        const appear = 10 + i * 8;
        const op = interpolate(frame, [appear, appear + 12], [0, 1], { extrapolateRight: "clamp" });
        const dx = interpolate(frame, [appear, appear + 12], [40, 0], { extrapolateRight: "clamp" });
        return (
          <div key={i} style={{ opacity: op, transform: `translateX(${dx}px)`,
            fontSize: 52, color: "#eaf2ff", margin: "14px 0", display: "flex", alignItems: "center" }}>
            <span style={{ color: brand, marginRight: 20, fontSize: 44 }}>●</span>{b}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

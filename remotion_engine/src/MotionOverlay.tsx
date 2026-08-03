import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { MotionOverlaySpec, MotionPosition, DEFAULT_FONT } from "./types";

// 差异化动效叠加：按 style 渲染不同的进出场动效与布局。
// 由 final_edit._build_motion_overlay 编译 shot.motionOverlay 后驱动。

function posStyle(position?: MotionPosition): React.CSSProperties {
  switch (position) {
    case "top": return { justifyContent: "flex-start", alignItems: "center", paddingTop: 140 };
    case "bottom": return { justifyContent: "flex-end", alignItems: "center", paddingBottom: 180 };
    case "lower_third": return { justifyContent: "flex-end", alignItems: "flex-start", paddingBottom: 260, paddingLeft: 80 };
    case "left": return { justifyContent: "center", alignItems: "flex-start", paddingLeft: 80 };
    case "right": return { justifyContent: "center", alignItems: "flex-end", paddingRight: 80 };
    case "corner": return { justifyContent: "flex-start", alignItems: "flex-end", paddingTop: 160, paddingRight: 70 };
    case "center":
    default: return { justifyContent: "center", alignItems: "center" };
  }
}

export const MotionOverlay: React.FC<{ spec: MotionOverlaySpec; brand: string; font: string }> = ({
  spec, brand, font,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ff = font || DEFAULT_FONT;
  const base: React.CSSProperties = { fontFamily: ff, boxSizing: "border-box" };

  // 通用进场 spring
  const appear = spring({ frame, fps, config: { damping: 200 }, durationInFrames: 14 });

  if (spec.style === "lower_third") {
    const slideX = interpolate(appear, [0, 1], [-120, 0]);
    const op = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
    return (
      <AbsoluteFill style={{ ...base, ...posStyle(spec.position || "lower_third") }}>
        <div style={{ opacity: op, transform: `translateX(${slideX}px)`,
          background: "rgba(0,0,0,0.55)", borderLeft: `10px solid ${brand}`,
          padding: "22px 42px", borderRadius: 6, maxWidth: "80%" }}>
          {spec.title && <div style={{ fontSize: 60, fontWeight: 800, color: "#fff" }}>{spec.title}</div>}
          {(spec.bullets || []).slice(0, 1).map((b, i) => (
            <div key={i} style={{ fontSize: 40, color: "#eaf2ff", marginTop: 8 }}>{b}</div>
          ))}
        </div>
      </AbsoluteFill>
    );
  }

  if (spec.style === "data_card" || spec.style === "metric_pop") {
    const scale = interpolate(appear, [0, 1], [0.7, 1]);
    const op = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
    const val = spec.metric?.value || spec.title || "";
    const label = spec.metric?.label || (spec.bullets && spec.bullets[0]) || "";
    return (
      <AbsoluteFill style={{ ...base, ...posStyle(spec.position || (spec.style === "data_card" ? "corner" : "center")) }}>
        <div style={{ opacity: op, transform: `scale(${scale})`,
          background: "rgba(11,18,32,0.82)", border: `3px solid ${brand}`,
          padding: "30px 46px", borderRadius: 20, textAlign: "center",
          boxShadow: "0 12px 40px rgba(0,0,0,0.45)" }}>
          <div style={{ fontSize: 108, fontWeight: 900, color: brand, lineHeight: 1 }}>{val}</div>
          {label && <div style={{ fontSize: 40, color: "#fff", marginTop: 12 }}>{label}</div>}
        </div>
      </AbsoluteFill>
    );
  }

  if (spec.style === "keyword_flash") {
    // 快闪：快速放大后回弹，短促强调
    const s = spring({ frame, fps, config: { damping: 12, stiffness: 220 }, durationInFrames: 12 });
    const scale = interpolate(s, [0, 1], [1.5, 1]);
    const op = interpolate(frame, [0, 5], [0, 1], { extrapolateRight: "clamp" });
    const word = spec.title || (spec.bullets && spec.bullets[0]) || "";
    return (
      <AbsoluteFill style={{ ...base, ...posStyle(spec.position || "center") }}>
        <div style={{ opacity: op, transform: `scale(${scale})`,
          fontSize: 120, fontWeight: 900, color: "#fff",
          textShadow: `0 0 30px ${brand}, 0 6px 24px rgba(0,0,0,0.6)`,
          letterSpacing: 4 }}>{word}</div>
      </AbsoluteFill>
    );
  }

  if (spec.style === "bullet_list") {
    return (
      <AbsoluteFill style={{ ...base, ...posStyle(spec.position || "center"), flexDirection: "column", padding: 90 }}>
        {spec.title && (
          <div style={{ fontSize: 76, fontWeight: 800, color: "#fff", marginBottom: 36,
            borderLeft: `12px solid ${brand}`, paddingLeft: 26,
            textShadow: "0 4px 24px rgba(0,0,0,0.45)" }}>{spec.title}</div>
        )}
        {(spec.bullets || []).map((b, i) => {
          const at = 8 + i * 8;
          const op = interpolate(frame, [at, at + 12], [0, 1], { extrapolateRight: "clamp" });
          const dx = interpolate(frame, [at, at + 12], [50, 0], { extrapolateRight: "clamp" });
          return (
            <div key={i} style={{ opacity: op, transform: `translateX(${dx}px)`,
              fontSize: 52, color: "#eaf2ff", margin: "14px 0", display: "flex", alignItems: "center" }}>
              <span style={{ color: brand, marginRight: 20 }}>●</span>{b}
            </div>
          );
        })}
      </AbsoluteFill>
    );
  }

  // title_reveal（默认）
  const scale = interpolate(appear, [0, 1], [1.12, 1]);
  const op = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ ...base, ...posStyle(spec.position || "center") }}>
      <div style={{ opacity: op, transform: `scale(${scale})`, textAlign: "center", padding: 80 }}>
        {spec.title && (
          <div style={{ fontSize: 96, fontWeight: 900, color: "#fff",
            textShadow: `0 6px 30px rgba(0,0,0,0.55)`, borderBottom: `8px solid ${brand}`,
            display: "inline-block", paddingBottom: 14 }}>{spec.title}</div>
        )}
        {(spec.bullets || []).slice(0, 1).map((b, i) => (
          <div key={i} style={{ fontSize: 46, color: "#eaf2ff", marginTop: 28 }}>{b}</div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

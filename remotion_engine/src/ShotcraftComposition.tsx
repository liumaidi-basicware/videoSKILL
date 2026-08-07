import React from "react";
import {AbsoluteFill, Img, Sequence, Video, interpolate, staticFile, useCurrentFrame, useVideoConfig} from "remotion";

type CardShot = {
  id: string;
  card_id: string;
  durationInFrames: number;
  source?: string;
  assets?: string[];
  postproduction?: {timing?: {intro_seconds?: number; impact_seconds?: number; hold_seconds?: number}};
};
export type ShotcraftSpec = {
  width: number; height: number; fps: number;
  theme?: {brand?: {primary?: string; accent?: string; background?: string; text?: string}};
  shots: CardShot[];
};

const resolve = (value: string) => /^(https?:|\/|file:)/.test(value) ? value : staticFile(value);
const media = (source?: string) => source && /\.(mp4|mov|webm|mkv)$/i.test(source);

const Source: React.FC<{source?: string}> = ({source}) => source ? (
  media(source) ? <Video src={resolve(source)} style={{width: "100%", height: "100%", objectFit: "cover"}} /> :
  <Img src={resolve(source)} style={{width: "100%", height: "100%", objectFit: "cover"}} />
) : null;

const Card: React.FC<{shot: CardShot; theme: NonNullable<ShotcraftSpec["theme"]>}> = ({shot, theme}) => {
  const frame = useCurrentFrame();
  const {durationInFrames, fps} = useVideoConfig();
  const p = durationInFrames > 1 ? frame / (durationInFrames - 1) : 1;
  const primary = theme.brand?.primary || "#0C66E4";
  const accent = theme.brand?.accent || "#36B37E";
  const intro = Math.max(1, Math.round((shot.postproduction?.timing?.intro_seconds || 0.45) * fps));
  const reveal = interpolate(frame, [0, intro], [0, 1], {extrapolateRight: "clamp"});
  const zoom = interpolate(p, [0, 1], [1.02, 1.12]);
  const isCompare = shot.card_id === "before-after-slider-scrub";
  const isFrame = shot.card_id === "brand-frame-snap" || shot.card_id === "logo-sting-button";
  const isLine = shot.card_id === "line-carry-transition" || shot.card_id === "shared-element-morph";
  const isData = shot.card_id === "data-viz-landscape-open" || shot.card_id === "timeline-travel" || shot.card_id === "document-typewriter-reveal";
  const split = interpolate(frame, [0, durationInFrames - 1], [0.18, 0.82], {extrapolateRight: "clamp"});
  return <AbsoluteFill style={{background: theme.brand?.background || "#0B1020", overflow: "hidden"}}>
    <AbsoluteFill style={{transform: `scale(${zoom})`, transformOrigin: "center"}}><Source source={shot.source} /></AbsoluteFill>
    {isCompare && <AbsoluteFill style={{clipPath: `inset(0 ${(1 - split) * 100}% 0 0)`}}><Source source={shot.assets?.[1] || shot.source} /></AbsoluteFill>}
    {isCompare && <div style={{position:"absolute", left:`${split * 100}%`, top:0, bottom:0, width:6, background:"#fff", boxShadow:"0 0 22px #000"}} />}
    {isLine && <div style={{position:"absolute", left:0, top:"50%", width:`${reveal * 100}%`, height:8, background:accent, boxShadow:`0 0 24px ${accent}`}} />}
    {isFrame && <div style={{position:"absolute", inset:Math.round(48 * (1 - reveal)), border:`8px solid ${primary}`, opacity:reveal, borderRadius:36}} />}
    {isData && <div style={{position:"absolute", inset:"14% 10%", border:`2px solid ${primary}`, background:"rgba(6,12,28,.68)", borderRadius:28, opacity:reveal, transform:`translateY(${(1-reveal)*50}px)`}} />}
    <div style={{position:"absolute", inset:0, background:`linear-gradient(135deg, ${primary}22, transparent 45%, ${accent}22)`, opacity:reveal}} />
  </AbsoluteFill>;
};

export const ShotcraftComposition: React.FC<ShotcraftSpec> = ({shots, theme = {}}) => <AbsoluteFill>
  {shots.map((shot, index) => <Sequence key={shot.id || index} from={shots.slice(0, index).reduce((sum, item) => sum + item.durationInFrames, 0)} durationInFrames={shot.durationInFrames}>
    <Card shot={shot} theme={theme} />
  </Sequence>)}
</AbsoluteFill>;

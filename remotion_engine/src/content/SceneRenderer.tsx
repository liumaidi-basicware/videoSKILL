import React from "react";
import { ContentScene } from "./contentTypes";
import {
  HeroTitle,
  SectionTitle,
  AnimatedList,
  FeatureGrid,
  MetricRow,
  DataTable,
  TypewriterScene,
  HighlightQuote,
  ProcessFlow,
  EvolutionTree,
  ComparisonCards,
  CausalGraph,
  ProductIntro,
} from "../vendor/components/new";

// 把一个 ContentScene 映射到对应的 vendored 组件。
// props 直接透传（由 content_scaffold.py 保证形状与组件签名对齐）。
export const SceneRenderer: React.FC<{ scene: ContentScene }> = ({ scene }) => {
  const p = scene.props as Record<string, unknown>;
  switch (scene.kind) {
    case "hero":
      return <HeroTitle {...(p as any)} />;
    case "section":
      return <SectionTitle {...(p as any)} />;
    case "list":
      return <AnimatedList {...(p as any)} />;
    case "features":
      return <FeatureGrid {...(p as any)} />;
    case "metrics":
      return <MetricRow {...(p as any)} />;
    case "table":
      return <DataTable {...(p as any)} />;
    case "typewriter":
      return <TypewriterScene {...(p as any)} />;
    case "quote":
      return <HighlightQuote {...(p as any)} />;
    case "process":
      return <ProcessFlow {...(p as any)} />;
    case "evolution":
      return <EvolutionTree {...(p as any)} />;
    case "comparison":
      return <ComparisonCards {...(p as any)} />;
    case "causal":
      return <CausalGraph {...(p as any)} />;
    case "product":
      return <ProductIntro {...(p as any)} />;
    default:
      return null;
  }
};

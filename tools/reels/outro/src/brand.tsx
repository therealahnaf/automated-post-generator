/** Shared brand geometry, deterministic easing and text helpers for the reel outro. */
import React, {useEffect, useState} from 'react';
import {continueRender, delayRender, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

// tokens.css and generate_post.py: the only brand colours in the repository.
export const CORAL = '#ff5757';
export const MINT = '#c2ffe1';
export const INK = '#212121';
export const DEEP = '#191d1f';
export const WHITE = '#fafaf8';
export const FONT = 'BitsRoboto';

// Keep in step with OUTRO_DURATION and OUTRO_TITLE in generate_reel.py.
export const OUTRO_SECONDS = 3;
export const OUTRO_TITLE_LINES = ['Follow The Bits Today', 'For More'];

type Point = [number, number];

// Corners fitted to bitstodaylogo-trans.png, in that PNG's 648x606 pixel space.
export const LOGO = {
  width: 648,
  height: 606,
  center: [357.0, 290.6] as Point,
  angle: 26.15,
  coral: [[257.6, -0.4], [648.0, 191.3], [456.3, 581.6], [65.9, 389.9]] as Point[],
  mint: [[183.4, 36.2], [568.7, 220.1], [384.8, 605.4], [-0.5, 421.5]] as Point[],
  // Clockwise, alternating dot and dash.
  bits: [
    [[444.6, 164.8], [472.6, 178.8], [458.4, 207.2], [430.4, 193.2]],
    [[531.2, 207.6], [559.6, 221.6], [511.3, 319.6], [482.9, 305.7]],
    [[454.2, 364.6], [482.2, 378.6], [468.2, 406.6], [440.2, 392.6]],
    [[341.6, 416.8], [439.9, 465.3], [425.9, 493.7], [327.6, 445.2]],
    [[254.8, 374.4], [282.8, 388.4], [268.8, 416.4], [240.8, 402.4]],
    [[202.1, 261.6], [230.5, 275.5], [182.2, 373.9], [153.8, 360.0]],
    [[245.0, 175.0], [273.0, 189.0], [259.0, 217.0], [231.0, 203.0]],
    [[287.4, 87.8], [385.7, 136.0], [371.8, 164.4], [273.5, 116.2]],
  ] as Point[][],
};

export const pointsD = (points: Point[]) => `M${points.map(([x, y]) => `${x} ${y}`).join('L')}Z`;

export const polygonCenter = (points: Point[]): Point => [
  points.reduce((sum, [x]) => sum + x, 0) / points.length,
  points.reduce((sum, [, y]) => sum + y, 0) / points.length,
];

export const squareD = (half: number) => `M${-half} ${-half}H${half}V${half}H${-half}Z`;

export const useTime = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return frame / fps;
};

export const clamp = (value: number, low = 0, high = 1) => Math.min(high, Math.max(low, value));
export const lerp = (from: number, to: number, amount: number) => from + (to - from) * amount;
export const seg = (time: number, start: number, end: number, ease: (x: number) => number = (x) => x) =>
  ease(clamp((time - start) / (end - start)));

export const outCubic = (x: number) => 1 - (1 - x) ** 3;
export const outQuint = (x: number) => 1 - (1 - x) ** 5;
export const inCubic = (x: number) => x ** 3;
export const inOutCubic = (x: number) => (x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2);
export const outBack = (x: number, overshoot = 1.70158) =>
  1 + (overshoot + 1) * (x - 1) ** 3 + overshoot * (x - 1) ** 2;

/** Seeded mulberry32, so every generated layout is identical in every render process. */
export const rng = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let value = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
  return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
};

export const measure = (text: string, size: number, weight: number) => {
  const context = document.createElement('canvas').getContext('2d')!;
  context.font = `${weight} ${size}px ${FONT}`;
  return context.measureText(text).width;
};

export const FontGate: React.FC<{children: React.ReactNode}> = ({children}) => {
  const [ready, setReady] = useState(false);
  const [handle] = useState(() => delayRender('Loading brand font'));
  useEffect(() => {
    const font = new FontFace(FONT, `url("${staticFile('Roboto-Variable.ttf')}")`, {weight: '100 900'});
    font.load().then((loaded) => {
      document.fonts.add(loaded);
      setReady(true);
      continueRender(handle);
    });
  }, [handle]);
  return ready ? <>{children}</> : null;
};

type OutlinedProps = {
  x: number;
  y: number;
  size: number;
  weight?: number;
  outline: number;
  anchor?: 'start' | 'middle' | 'end';
  opacity?: number;
  transform?: string;
  children: React.ReactNode;
};

/** Bold white type with a black outline painted behind the fill. */
export const Outlined: React.FC<OutlinedProps> = ({x, y, size, weight = 900, outline, anchor = 'middle', opacity = 1, transform, children}) => (
  <text x={x} y={y} fontFamily={FONT} fontSize={size} fontWeight={weight} textAnchor={anchor} fill={WHITE}
    stroke="#000" strokeWidth={outline} strokeLinejoin="round" paintOrder="stroke" opacity={opacity} transform={transform}>
    {children}
  </text>
);

type WordsProps = {
  line: string;
  y: number;
  size: number;
  outline: number;
  start: number;
  stagger: number;
  time: number;
  centerX?: number;
};

/** One centred line whose words rise into place on their own beats. */
export const RisingWords: React.FC<WordsProps> = ({line, y, size, outline, start, stagger, time, centerX = 540}) => {
  const words = line.split(' ');
  const space = measure(' ', size, 900);
  const widths = words.map((word) => measure(word, size, 900));
  const total = widths.reduce((sum, width) => sum + width, 0) + space * (words.length - 1);
  let cursor = centerX - total / 2;
  return <>{words.map((word, index) => {
    const x = cursor;
    cursor += widths[index] + space;
    const progress = seg(time, start + index * stagger, start + index * stagger + 0.3, outQuint);
    return <Outlined key={word + index} x={x} y={y + (1 - progress) * size * 0.8} size={size} outline={outline}
      anchor="start" opacity={seg(time, start + index * stagger, start + index * stagger + 0.1)}>{word}</Outlined>;
  })}</>;
};

export const Stage: React.FC<{children: React.ReactNode; defs?: React.ReactNode}> = ({children, defs}) => (
  <svg width={1080} height={1920} viewBox="0 0 1080 1920" xmlns="http://www.w3.org/2000/svg"
    style={{position: 'absolute', inset: 0, background: 'transparent'}}>
    {defs ? <defs>{defs}</defs> : null}
    {children}
  </svg>
);

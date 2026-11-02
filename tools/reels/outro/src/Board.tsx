/** The reel outro: the logo lands as a chip, the board spreads from the impact, and the chip boots. */
import React, {useMemo} from 'react';
import {
  CORAL, FONT, LOGO, MINT, OUTRO_TITLE_LINES, RisingWords, Stage,
  inCubic, inOutCubic, lerp, outBack, outCubic, outQuint, pointsD, polygonCenter, rng, seg, squareD, useTime,
} from './brand';

const CX = 540;
const CY = 618;
const LOGO_SCALE = 0.78;
const CHIP = 226;
const LEG = 46;
const PINS = 18;
const PITCH = 22;
const IMPACT = 0.34;

type Trace = {d: string; width: number; phase: number; speed: number; coral: boolean; end: [number, number] | null};

const buildTraces = (): Trace[] => {
  const random = rng(4077);
  const traces: Trace[] = [];
  for (let side = 0; side < 4; side++) {
    for (let pin = 0; pin < PINS; pin++) {
      const u = (pin - (PINS - 1) / 2) * PITCH;
      const start = CHIP + LEG + 4;
      const shoulder = start + 34 + Math.abs(pin - (PINS - 1) / 2) * 7;
      const fanned = u * 1.75;
      const afterFan = shoulder + Math.abs(fanned - u);
      const reach = random();
      const terminates = reach < 0.34;
      const stop = terminates ? afterFan + 90 + random() * 420 : 1900;
      const lane = afterFan + 150;
      const shifted = fanned + Math.sign(u || 1) * 44;
      const commands = [
        [u, -start], [u, -shoulder], [fanned, -afterFan],
        ...(stop > lane + 44 ? [[fanned, -lane], [shifted, -lane - 44], [shifted, -stop]] : [[fanned, -stop]]),
      ];
      const radians = (side * Math.PI) / 2;
      const rotated = commands.map(([x, y]) => [
        x * Math.cos(radians) - y * Math.sin(radians),
        x * Math.sin(radians) + y * Math.cos(radians),
      ] as [number, number]);
      traces.push({
        d: `M${rotated.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join('L')}`,
        width: pin % 7 === 3 ? 8 : 4,
        phase: random(),
        speed: 0.28 + random() * 0.2,
        coral: pin % 5 === 2,
        end: terminates ? rotated[rotated.length - 1] : null,
      });
    }
  }
  return traces;
};

const Silver: React.FC<{x: number; y: number; width: number; height: number}> = (box) =>
  <rect {...box} fill="url(#board-silver)"/>;

const Capacitor: React.FC<{x: number; y: number; turn: number; label?: string}> = ({x, y, turn, label}) => (
  <g transform={`translate(${x} ${y}) rotate(${turn})`}>
    <rect x={-34} y={-20} width={68} height={40} fill="none" stroke="#fff" strokeOpacity={0.3} strokeWidth={2}/>
    <rect x={-30} y={-15} width={60} height={30} rx={2} fill="#000" fillOpacity={0.5} transform="translate(3 4)"/>
    <rect x={-22} y={-14} width={44} height={28} fill="#9a8f7c"/>
    <rect x={-22} y={-14} width={44} height={9} fill="#fff" fillOpacity={0.18}/>
    <Silver x={-30} y={-15} width={11} height={30}/>
    <Silver x={19} y={-15} width={11} height={30}/>
    {label ? <text y={-27} textAnchor="middle" fontFamily={FONT} fontSize={18} fontWeight={700} fill="#fff" fillOpacity={0.55}>{label}</text> : null}
  </g>
);

const Resistor: React.FC<{x: number; y: number; turn: number}> = ({x, y, turn}) => (
  <g transform={`translate(${x} ${y}) rotate(${turn})`}>
    <rect x={-27} y={-12} width={54} height={24} fill="#000" fillOpacity={0.5} transform="translate(2 3)"/>
    <rect x={-19} y={-12} width={38} height={24} fill="#121415"/>
    <Silver x={-27} y={-12} width={9} height={24}/>
    <Silver x={18} y={-12} width={9} height={24}/>
    <text y={6} textAnchor="middle" fontFamily={FONT} fontSize={14} fontWeight={700} fill="#fff" fillOpacity={0.7}>103</text>
  </g>
);

const Electrolytic: React.FC<{x: number; y: number; radius: number}> = ({x, y, radius}) => (
  <g transform={`translate(${x} ${y})`}>
    <circle r={radius + 16} fill="none" stroke="#fff" strokeOpacity={0.3} strokeWidth={2.5}/>
    <circle r={radius + 4} cx={8} cy={12} fill="#000" fillOpacity={0.55}/>
    <circle r={radius} fill="url(#board-can)"/>
    <circle r={radius * 0.84} fill="none" stroke="#000" strokeOpacity={0.5} strokeWidth={3}/>
    <path d={`M${-radius * 0.62} 0H${radius * 0.62}M0 ${-radius * 0.62}V${radius * 0.62}`} stroke="#000" strokeOpacity={0.6} strokeWidth={5}/>
    <path d={`M0 ${-radius}A${radius} ${radius} 0 0 1 ${radius} 0L${radius * 0.84} 0A${radius * 0.84} ${radius * 0.84} 0 0 0 0 ${-radius * 0.84}Z`} fill={CORAL}/>
  </g>
);

const Crystal: React.FC<{x: number; y: number; turn: number}> = ({x, y, turn}) => (
  <g transform={`translate(${x} ${y}) rotate(${turn})`}>
    <rect x={-74} y={-36} width={148} height={72} rx={36} fill="#000" fillOpacity={0.5} transform="translate(6 8)"/>
    <rect x={-74} y={-36} width={148} height={72} rx={36} fill="url(#board-silver)"/>
    <rect x={-64} y={-26} width={128} height={52} rx={26} fill="none" stroke="#fff" strokeOpacity={0.5} strokeWidth={2}/>
    <rect x={-46} y={-22} width={92} height={7} rx={3} fill="#fff" fillOpacity={0.75}/>
  </g>
);

const SmallChip: React.FC<{x: number; y: number; turn: number; size: number}> = ({x, y, turn, size}) => (
  <g transform={`translate(${x} ${y}) rotate(${turn})`}>
    <rect x={-size - 9} y={-size - 9} width={size * 2 + 18} height={size * 2 + 18} fill="none" stroke="#fff" strokeOpacity={0.25} strokeWidth={2}/>
    {Array.from({length: 6}, (_, pad) => -size * 0.75 + (pad * size * 1.5) / 5).flatMap((offset) => [
      <Silver key={`l${offset}`} x={-size - 8} y={offset - 4} width={size * 2 + 16} height={8}/>,
    ])}
    <rect x={-size + 3} y={-size + 3} width={size * 2} height={size * 2} fill="#000" fillOpacity={0.5}/>
    <rect x={-size} y={-size} width={size * 2} height={size * 2} rx={3} fill="url(#board-body)"/>
    <circle cx={-size * 0.62} cy={-size * 0.62} r={size * 0.11} fill="#000" fillOpacity={0.7}/>
  </g>
);

const Header: React.FC<{x: number; y: number; turn: number; pins: number}> = ({x, y, turn, pins}) => (
  <g transform={`translate(${x} ${y}) rotate(${turn})`}>
    <rect x={-22} y={-22} width={pins * 44} height={88} fill="#0c0e0f" stroke="#fff" strokeOpacity={0.28} strokeWidth={2}/>
    {Array.from({length: pins}, (_, column) => [0, 44].map((row) => <g key={`${column}-${row}`} transform={`translate(${column * 44} ${row})`}>
      <circle r={16} fill="url(#board-silver)"/>
      <rect x={-7} y={-7} width={14} height={14} fill="#c9cfd2"/>
      <rect x={-7} y={-7} width={14} height={5} fill="#fff" fillOpacity={0.7}/>
    </g>))}
  </g>
);

const Via: React.FC<{x: number; y: number; size?: number}> = ({x, y, size = 10}) => <g transform={`translate(${x} ${y})`}>
  <circle r={size} fill="url(#board-silver)"/>
  <circle r={size * 0.46} fill="#070808"/>
</g>;

const StaticBoard: React.FC<{traces: Trace[]}> = React.memo(({traces}) => {
  const vias = useMemo(() => {
    const random = rng(911);
    return Array.from({length: 150}, () => {
      const x = (random() - 0.5) * 2500;
      const y = (random() - 0.5) * 2500;
      return {x, y, size: random() < 0.2 ? 13 : 8};
    }).filter(({x, y}) => Math.max(Math.abs(x), Math.abs(y)) > 340 && Math.abs(Math.abs(x) - Math.abs(y)) < 620);
  }, []);
  const quadrants: [number, number][] = [[1, 1], [-1, -1], [1, -1], [-1, 1]];
  return <>
    <rect x={-2200} y={-2200} width={4400} height={4400} fill="url(#board-mask)"/>
    <rect x={-2200} y={-2200} width={4400} height={4400} fill="url(#board-fiber)"/>
    {quadrants.map(([x, y]) => <path key={`${x}${y}`} transform={`scale(${x} ${y})`} fill="url(#board-pour)"
      d="M470 470L1500 470L1500 1500L470 1500Z"/>)}
    {quadrants.map(([x, y]) => <path key={`e${x}${y}`} transform={`scale(${x} ${y})`} fill="none" stroke={MINT} strokeOpacity={0.14} strokeWidth={3}
      d="M470 470L1500 470M470 470L470 1500"/>)}
    <g fill="none" strokeLinejoin="round">
      {traces.map((trace, index) => <path key={`shade-${index}`} d={trace.d} stroke="#000" strokeOpacity={0.55} strokeWidth={trace.width + 5} transform="translate(2 3)"/>)}
      {traces.map((trace, index) => <path key={`copper-${index}`} d={trace.d} stroke="#3f7a68" strokeWidth={trace.width}/>)}
      {traces.map((trace, index) => <path key={`shine-${index}`} d={trace.d} stroke={MINT} strokeOpacity={0.22} strokeWidth={Math.max(1.2, trace.width * 0.3)}/>)}
    </g>
    {traces.filter((trace) => trace.end).map((trace, index) => <Via key={index} x={trace.end![0]} y={trace.end![1]} size={trace.width > 4 ? 13 : 10}/>)}
    {vias.map(({x, y, size}, index) => <Via key={index} x={x} y={y} size={size}/>)}
    <Electrolytic x={640} y={520} radius={108}/>
    <Electrolytic x={-690} y={-560} radius={92}/>
    <Electrolytic x={930} y={790} radius={76}/>
    <Electrolytic x={500} y={980} radius={64}/>
    <Crystal x={830} y={490} turn={90}/>
    <Crystal x={-560} y={-800} turn={0}/>
    <SmallChip x={585} y={760} turn={0} size={62}/>
    <SmallChip x={-880} y={-760} turn={0} size={74}/>
    <SmallChip x={-540} y={560} turn={0} size={50}/>
    <SmallChip x={600} y={-560} turn={90} size={56}/>
    <Header x={760} y={990} turn={0} pins={7}/>
    <Header x={-1040} y={-520} turn={90} pins={6}/>
    {[[520, 640, 0, 'C14'], [720, 640, 90, 'C15'], [860, 640, 0, 'C16'], [-560, -640, 90, 'C7'], [-780, -520, 0, 'C8'],
      [-500, -930, 0, 'C9'], [1010, 560, 90, 'C21'], [610, 900, 0, 'C22'], [404, -404, 45, 'C3'], [-404, 404, 45, 'C4']].map(([x, y, turn, label]) =>
      <Capacitor key={label as string} x={x as number} y={y as number} turn={turn as number} label={label as string}/>)}
    {[[470, 830, 90], [720, 840, 0], [860, 860, 90], [-640, -930, 90], [-760, -930, 90], [-880, -930, 90], [-470, -470, 45],
      [480, 470, 45], [-1000, -860, 0], [680, 400, 0], [352, -352, 45], [-352, 352, 45]].map(([x, y, turn], index) =>
      <Resistor key={index} x={x} y={y} turn={turn}/>)}
    {[[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([x, y]) => <g key={`${x}${y}`} transform={`translate(${x * 1180} ${y * 1180})`}>
      <circle r={74} fill="url(#board-silver)"/>
      <circle r={44} fill="#050606"/>
    </g>)}
    <text x={700} y={1130} fontFamily={FONT} fontSize={30} fontWeight={800} letterSpacing={4} fill="#fff" fillOpacity={0.5}>THE BITS TODAY</text>
    <text x={-860} y={-1000} fontFamily={FONT} fontSize={26} fontWeight={800} letterSpacing={3} fill="#fff" fillOpacity={0.45}>HOW DID 1s AND 0s LEARN TO THINK?</text>
  </>;
});

const Pulses: React.FC<{traces: Trace[]; time: number}> = ({traces, time}) => {
  const burst = seg(time, IMPACT, IMPACT + 0.9, outCubic);
  const burstOpacity = seg(time, IMPACT, IMPACT + 0.1) * (1 - seg(time, IMPACT + 0.55, IMPACT + 0.9));
  const inward = seg(time, 0.95, 1.3);
  return <g fill="none">
    {burstOpacity > 0 ? traces.map((trace, index) => <path key={`burst-${index}`} d={trace.d} pathLength={1} stroke={trace.coral ? CORAL : MINT}
      strokeWidth={trace.width + 2} strokeDasharray="0.16 0.84" strokeDashoffset={0.16 - burst * 1.1 - trace.phase * 0.06} opacity={burstOpacity}/>) : null}
    {inward > 0 ? traces.map((trace, index) => {
      const offset = (time - 0.9) * trace.speed + trace.phase;
      const color = trace.coral ? CORAL : MINT;
      return <React.Fragment key={index}>
        <path d={trace.d} pathLength={1} stroke={color} strokeOpacity={0.28 * inward} strokeWidth={trace.width + 9} strokeLinecap="round"
          strokeDasharray="0.05 0.45" strokeDashoffset={offset}/>
        <path d={trace.d} pathLength={1} stroke={color} strokeOpacity={inward} strokeWidth={trace.width} strokeLinecap="round"
          strokeDasharray="0.05 0.45" strokeDashoffset={offset}/>
      </React.Fragment>;
    }) : null}
  </g>;
};

const StatusLeds: React.FC<{time: number}> = ({time}) => {
  const tick = Math.floor(time * 11);
  return <g transform="translate(-842 -452)">
    <rect x={-22} y={-26} width={8 * 46 + 18} height={52} fill="#000" fillOpacity={0.35} stroke="#fff" strokeOpacity={0.24} strokeWidth={2}/>
    {Array.from({length: 8}, (_, bit) => {
      const lit = time > 0.9 && ((tick * 37 + bit * bit * 13 + tick * bit) % 5) < 3;
      const color = bit % 3 === 2 ? CORAL : MINT;
      return <g key={bit} transform={`translate(${bit * 46} 0)`}>
        {lit ? <circle r={30} fill={color} fillOpacity={0.2}/> : null}
        <rect x={-13} y={-9} width={26} height={18} rx={2} fill={lit ? color : '#2c3336'}/>
      </g>;
    })}
  </g>;
};

const Chip: React.FC<{time: number}> = ({time}) => {
  const fall = seg(time, 0, IMPACT, inCubic);
  const height = 1 - fall;
  const scale = 1 + height * 1.55;
  const settle = seg(time, IMPACT, IMPACT + 0.2, (x) => outBack(x, 3));
  const squash = time < IMPACT ? 1 : lerp(0.96, 1, settle);
  const finalScale = scale * squash;
  const legs = Array.from({length: PINS}, (_, pin) => (pin - (PINS - 1) / 2) * PITCH);
  const [centerX, centerY] = [LOGO.width / 2, LOGO.height / 2];
  const glow = seg(time, 1.4, 1.7, outCubic) * (0.8 + Math.sin(time * 9) * 0.2);
  return <>
    <g transform={`translate(${CX + height * 90} ${CY + height * 130}) rotate(${LOGO.angle}) scale(${lerp(1.05, 1.9, height)})`} opacity={lerp(0.75, 0.18, height) * seg(time, 0, 0.1)}>
      <rect x={-CHIP - 34} y={-CHIP - 34} width={(CHIP + 34) * 2} height={(CHIP + 34) * 2} fill="#000" filter="url(#board-soft)"/>
    </g>
    {glow > 0 ? <g transform={`translate(${CX} ${CY}) rotate(${LOGO.angle})`}>
      <rect x={-CHIP - 70} y={-CHIP - 70} width={(CHIP + 70) * 2} height={(CHIP + 70) * 2} fill={MINT} fillOpacity={0.12 * glow} filter="url(#board-soft)"/>
    </g> : null}
    <g transform={`translate(${CX} ${CY}) rotate(${LOGO.angle + height * 24}) scale(${finalScale})`}>
      {[0, 1, 2, 3].map((side) => <g key={side} transform={`rotate(${side * 90})`}>
        {legs.map((u) => <g key={u}>
          <rect x={u - 8} y={-CHIP - LEG - 6} width={16} height={LEG + 8} fill="#000" fillOpacity={0.5} transform="translate(3 4)"/>
          <rect x={u - 7} y={-CHIP - LEG - 6} width={14} height={LEG + 8} fill="url(#board-silver)"/>
          <rect x={u - 7} y={-CHIP - 22} width={14} height={7} fill="#fff" fillOpacity={0.7}/>
        </g>)}
      </g>)}
      <rect x={-CHIP} y={-CHIP} width={CHIP * 2} height={CHIP * 2} rx={12} fill="url(#board-body)"/>
      <rect x={-CHIP + 10} y={-CHIP + 10} width={CHIP * 2 - 20} height={CHIP * 2 - 20} rx={6} fill="none" stroke="#fff" strokeOpacity={0.12} strokeWidth={3}/>
      <circle cx={-CHIP + 32} cy={-CHIP + 32} r={11} fill="#050606"/>
    </g>
    <g transform={`translate(${CX} ${CY}) rotate(${height * 24}) scale(${finalScale * LOGO_SCALE}) translate(${-centerX} ${-centerY})`}>
      <path d={pointsD(LOGO.mint)} fill={MINT}/>
      <path d={pointsD(LOGO.coral)} fill={CORAL}/>
      {LOGO.bits.map((bit, index) => {
        const on = seg(time, 0.84 + index * 0.075, 0.92 + index * 0.075);
        const [x, y] = polygonCenter(bit);
        return <g key={index}>
          <path d={pointsD(bit)} fill="#8f2c2c"/>
          <path d={pointsD(bit)} fill={MINT} opacity={on}/>
          <circle cx={x} cy={y} r={46} fill={MINT} opacity={(1 - seg(time, 0.92 + index * 0.075, 1.22 + index * 0.075)) * on * 0.5} filter="url(#board-soft)"/>
        </g>;
      })}
    </g>
  </>;
};

export const Board: React.FC = () => {
  const time = useTime();
  const traces = useMemo(buildTraces, []);
  const spread = seg(time, IMPACT - 0.01, IMPACT + 0.56, outQuint);
  const reach = 150 + spread * 1800;
  const shake = (1 - seg(time, IMPACT, IMPACT + 0.26)) * (time >= IMPACT ? 1 : 0);
  const shakeX = Math.sin(time * 91) * 13 * shake;
  const shakeY = Math.cos(time * 73) * 15 * shake;
  const push = 1 + time * 0.016;
  const sweep = seg(time, 0.95, 2.5, inOutCubic);
  const defs = <>
    <linearGradient id="board-silver" x1={0} y1={0} x2={1} y2={1}>
      <stop offset="0" stopColor="#f2f5f5"/>
      <stop offset="0.45" stopColor="#9aa4a8"/>
      <stop offset="1" stopColor="#d5dbdd"/>
    </linearGradient>
    <linearGradient id="board-body" x1={0} y1={0} x2={1} y2={1}>
      <stop offset="0" stopColor="#3b4245"/>
      <stop offset="0.55" stopColor="#202427"/>
      <stop offset="1" stopColor="#111315"/>
    </linearGradient>
    <radialGradient id="board-can" cx="0.36" cy="0.32" r="0.8">
      <stop offset="0" stopColor="#f6f8f8"/>
      <stop offset="0.5" stopColor="#a8b1b5"/>
      <stop offset="1" stopColor="#59626a"/>
    </radialGradient>
    <radialGradient id="board-mask" gradientUnits="userSpaceOnUse" cx={0} cy={0} r={1500}>
      <stop offset="0" stopColor="#1d3a33"/>
      <stop offset="0.45" stopColor="#14241f"/>
      <stop offset="1" stopColor="#0a100e"/>
    </radialGradient>
    <pattern id="board-fiber" width={14} height={14} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <path d="M0 7H14M7 0V14" stroke="#fff" strokeOpacity={0.022} strokeWidth={2}/>
    </pattern>
    <pattern id="board-pour" width={22} height={22} patternUnits="userSpaceOnUse">
      <path d="M0 0H22M0 0V22" stroke={MINT} strokeOpacity={0.09} strokeWidth={4}/>
    </pattern>
    <linearGradient id="board-shade" x1={0} y1={0} x2={0} y2={1}>
      <stop offset="0" stopColor="#000" stopOpacity={0}/>
      <stop offset="0.3" stopColor="#000" stopOpacity={0.72}/>
      <stop offset="0.75" stopColor="#000" stopOpacity={0.72}/>
      <stop offset="1" stopColor="#000" stopOpacity={0}/>
    </linearGradient>
    <linearGradient id="board-sheen" x1={0} y1={0} x2={1} y2={0}>
      <stop offset="0" stopColor="#fff" stopOpacity={0}/>
      <stop offset="0.5" stopColor={MINT} stopOpacity={0.13}/>
      <stop offset="1" stopColor="#fff" stopOpacity={0}/>
    </linearGradient>
    <filter id="board-soft" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation={30}/></filter>
    <clipPath id="board-spread"><path d={squareD(reach)}/></clipPath>
  </>;
  return <Stage defs={defs}>
    <g transform={`translate(${shakeX} ${shakeY})`}>
      <g transform={`translate(${CX} ${CY}) scale(${push}) rotate(${LOGO.angle})`}>
        <g clipPath="url(#board-spread)">
          <StaticBoard traces={traces}/>
          <StatusLeds time={time}/>
          <Pulses traces={traces} time={time}/>
          <rect x={-2200 + (sweep - 0.5) * 3800} y={-2200} width={520} height={4400} fill="url(#board-sheen)"/>
        </g>
        {spread < 1 ? <>
          <path d={squareD(reach)} fill="none" stroke={MINT} strokeOpacity={0.3} strokeWidth={38}/>
          <path d={squareD(reach)} fill="none" stroke={MINT} strokeWidth={8}/>
        </> : null}
      </g>
      <g transform={`translate(${CX} ${CY}) scale(${push}) translate(${-CX} ${-CY})`}>
        <Chip time={time}/>
      </g>
    </g>
    <rect x={0} y={905} width={1080} height={350} fill="url(#board-shade)" opacity={seg(time, 0.6, 0.95)}/>
    <RisingWords line={OUTRO_TITLE_LINES[0]} y={1048} size={84} outline={15} start={0.72} stagger={0.06} time={time}/>
    <RisingWords line={OUTRO_TITLE_LINES[1]} y={1142} size={84} outline={15} start={0.96} stagger={0.06} time={time}/>
  </Stage>;
};

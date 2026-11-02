import React from 'react';
import {AbsoluteFill, Composition, registerRoot} from 'remotion';
import {Board} from './Board';
import {FontGate, OUTRO_SECONDS} from './brand';

const Outro: React.FC = () => <FontGate>
  <AbsoluteFill style={{backgroundColor: 'transparent'}}><Board/></AbsoluteFill>
</FontGate>;

const FPS = 60;

const Root: React.FC = () => <Composition id="Board" component={Outro} width={1080} height={1920}
  fps={FPS} durationInFrames={OUTRO_SECONDS * FPS}/>;

registerRoot(Root);

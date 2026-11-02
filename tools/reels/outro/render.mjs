/** Render the Board outro into the colour and matte MP4s that generate_reel.py composites. */
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdtemp, rm} from 'node:fs/promises';
import {createServer} from 'node:net';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const project = path.resolve(here, '../../..');
const assets = path.resolve(here, '../assets');
const runtime = path.join(project, 'content_api/story_video/rendering');
const require = createRequire(path.join(runtime, 'package.json'));
const {bundle} = require('@remotion/bundler');
const {renderMedia, selectComposition} = require('@remotion/renderer');

const localBrowser = path.join(project, '.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe');
const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE || (existsSync(localBrowser) ? localBrowser : undefined);
const work = await mkdtemp(path.join(tmpdir(), 'reel-outro-'));
const serveUrl = await bundle({entryPoint: path.join(here, 'src/index.tsx'), rootDir: project,
  publicDir: path.join(project, 'assets/fonts'), outDir: path.join(work, 'bundle'), webpackOverride: (config) => {
    config.resolve.modules = ['node_modules', path.join(runtime, 'node_modules')];
    return config;
  }});

// Port 3000 hosts another app, so always ask the OS for a free one.
const socket = createServer();
await new Promise((resolve, reject) => {socket.once('error', reject); socket.listen(0, '127.0.0.1', resolve);});
const port = socket.address().port;
await new Promise((resolve, reject) => socket.close((error) => (error ? reject(error) : resolve())));

const composition = await selectComposition({serveUrl, id: 'Board', browserExecutable, port});
const master = path.join(work, 'board.mov');
await renderMedia({composition, serveUrl, browserExecutable, port, codec: 'prores', proResProfile: '4444',
  imageFormat: 'png', pixelFormat: 'yuva444p10le', outputLocation: master, overwrite: true, concurrency: 4,
  muted: true, onProgress: ({progress}) => process.stdout.write(`\rBoard ${Math.round(progress * 100)}%`)});

// A ProRes master is 370 MB, and H.264 pairs decode on every FFmpeg build the reels run on.
// The matte's values stay 0-255; clearing its full-range flag only stops it forcing the reel to full range.
const encode = (filter, pixelFormat, crf, output) => execFileSync('ffmpeg', ['-y', '-v', 'error', '-i', master,
  '-vf', filter, '-c:v', 'libx264', '-preset', 'slow', '-crf', crf, '-pix_fmt', pixelFormat, '-an',
  '-bsf:v', 'h264_metadata=video_full_range_flag=0', '-movflags', '+faststart', path.join(assets, output)], {stdio: 'inherit'});
encode('format=yuv444p', 'yuv444p', '17', 'outro-board-color.mp4');
encode('alphaextract,format=gray', 'gray', '12', 'outro-board-alpha.mp4');
await rm(work, {recursive: true, force: true});
console.log(`\nSaved ${assets}`);

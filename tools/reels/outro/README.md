# Board reel outro

The Remotion source for the three-second outro in `generate_reel.py`. Reels only
need FFmpeg: this folder renders the outro once, and the output is kept in
`tools/reels/assets/`:

- `outro-board-color.mp4` is the picture.
- `outro-board-alpha.mp4` is its transparency matte.

Both are 60fps, so FFmpeg can drop to any source frame rate while the motion
keeps its timing.

To change the copy or the animation, edit `src/`, then run this from the
repository root:

```bash
node tools/reels/outro/render.mjs
```

It uses the Remotion install in `content_api/story_video/rendering`. Keep
`OUTRO_SECONDS` and `OUTRO_TITLE_LINES` in `src/brand.tsx` in step with
`OUTRO_DURATION` and `OUTRO_TITLE` in `generate_reel.py`.
The asset hashes are recorded in each reel's JSON sidecar, so changed assets
cause old reels to re-render.

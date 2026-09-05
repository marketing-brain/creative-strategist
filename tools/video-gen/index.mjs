import fs from 'node:fs';
import { experimental_generateVideo as generateVideo } from 'ai';

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const opts = {
  model: arg('model'),
  imageUrl: arg('image-url'),
  prompt: arg('prompt'),
  duration: Number(arg('duration', '5')),
  aspectRatio: arg('aspect', '9:16'),
  out: arg('out'),
};

if (process.argv.includes('--dry-run')) {
  process.stdout.write(JSON.stringify(opts));
  process.exit(0);
}

for (const k of ['model', 'imageUrl', 'prompt', 'out']) {
  if (!opts[k]) { process.stderr.write(JSON.stringify({ error: `missing --${k}` })); process.exit(1); }
}

try {
  // A plain 'creator/model' string routes through AI Gateway. `poll` runs the job asynchronously so no single
  // request stays open for the whole generation; `prompt.image` must be a hosted URL for the async flow.
  const result = await generateVideo({
    model: opts.model,
    prompt: { image: opts.imageUrl, text: opts.prompt },
    duration: opts.duration,
    aspectRatio: opts.aspectRatio,
    poll: { intervalMs: 5000, timeoutMs: 15 * 60 * 1000 },
  });
  fs.writeFileSync(opts.out, result.videos[0].uint8Array);
  process.stdout.write(JSON.stringify({ ok: true, bytes: result.videos[0].uint8Array.length }));
} catch (err) {
  process.stderr.write(JSON.stringify({ error: String(err?.message ?? err) }));
  process.exit(1);
}

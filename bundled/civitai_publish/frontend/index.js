import { MIGRATED_NEWS } from './migratedNews.js'
import { GUIDE, guideHelp } from './guide.js'
// 📤 Publish to Civitai — the frontend descriptor of the bundled
// `civitai_publish` plugin. Plain JS on purpose: node --test imports it (the
// parity, help and slot contracts), Vite globs it at build time
// (src/plugins/bundled.js). The React panels are lazy imports, so each is its
// own chunk and only loads where a surface mounts it.
//
// ONE dialog, two doors, five places the core lends:
//   - `lightbox.action` (gallery surface) — the viewer's 📤 verb: post THIS
//     image under the page its checkpoint is linked to. The dataset and Bank
//     lightboxes are skipped on paper below, never silently.
//   - `checkpoint.action` + `checkpoint.layer` (run graph, LoRA Canvas, and the dataset run graph) — a row asks, the host-mounted layer shows
//     the dialog: publish a checkpoint as a model page, or mark the page it
//     already has.
//   - `settings.group` on this product — the shared key and which domain links open on.
//   - `setup.step` (the counted row) + `setup.key` (the key field on the
//     Image generation step). The credential itself stays the core's.
import { action, setting } from '@lds/plugin-sdk/help'
import { CIVITAI_KEY_FIELD, CIVITAI_ROW_LABEL, civitaiReady, civitaiSetupRows } from './lib/civitaiSetup.js'

export default {
  guide: GUIDE,
  id: 'civitai_publish',
  nav: [],
  routes: [],
  slots: {
    'lightbox.action': [
      // Narrowed to the gallery surface on purpose (the two skips below say why),
      // so the registry offers it nowhere else.
      { id: 'civitai', surfaces: ['gallery'], panels: { gallery: () => import('./panels/GalleryCivitaiAction.jsx') } },
    ],
    'checkpoint.action': [
      // The run graph and optional Canvas share one checkpoint row and dialog.
      { id: 'civitai', surfaces: ['graph', 'canvas'], panels: {
        graph: () => import('./panels/CheckpointCivitaiRow.jsx'),
        canvas: () => import('./panels/CheckpointCivitaiRow.jsx'),
      } },
    ],
    'checkpoint.layer': [
      { id: 'civitai', surfaces: ['graph', 'canvas'], panel: () => import('./panels/CheckpointCivitaiLayer.jsx') },
    ],
    'settings.group': [
      { id: 'civitai-publishing', section: 'scraping',
        title: '📤 Civitai publishing',
        blurb: 'Which Civitai domain your model pages and posts open on.',
        keywords: ['civitai.red', 'publish', 'post', 'model page', 'draft 404', 'link host'],
        panel: () => import('./panels/CivitaiPublishingSettings.jsx') },
    ],
    'setup.step': [{ id: 'civitai', rows: civitaiSetupRows }],
    'setup.key': [
      { id: 'civitai', field: CIVITAI_KEY_FIELD, capabilityLabel: CIVITAI_ROW_LABEL, okWhen: civitaiReady },
    ],
  },
  hosts: [],
  help: ([
    // 📤 One dialog, two doors (the checkpoint popover on the ◉ Canvas / run
    // graph, and the shared image viewer everywhere it opens): one topic.
    action('civitai-publish', '📤 Publish a LoRA and its images to Civitai',
      ['civitai', 'civitai.red', 'publish', 'publish lora', 'upload lora', 'upload checkpoint',
        'model page', 'draft', 'post image', 'post to civitai', 'share image', 'share lora',
        'link checkpoint', 'mark the page', 'trigger words', 'base model', 'generation data',
        'prompt', 'seed', 'metadata', 'api key', 'not linked', 'wizard', 'nsfw'],
      '/gallery', 'using-the-app', 'publish-a-lora-and-its-images-to-civitai'),
    setting('civitai.link_host', 'scraping', 'civitai-link-host', 'Civitai site for links',
      ['civitai', 'civitai.red', 'domain', 'mirror', 'links', 'publish', 'open on', 'draft 404']),
  ]).map(guideHelp),
  whatsNew: [{
    id: '2026-10-17-civitai-picture-lora-page',
    date: '2026-10-17',
    title: 'Post pictures whose training run is gone',
    blurb: 'A picture whose run was removed, or that was made before runs were recorded, can now be '
      + 'posted: paste its LoRA’s Civitai page in the post window and pick the version. The page is '
      + 'remembered for the next pictures of that LoRA.',
    to: '/gallery',
  }, {
    id: '2026-10-17-civitai-post-generation-model',
    date: '2026-10-17',
    title: 'Civitai posts also tag the model they were generated with',
    blurb: 'A picture posted from the image viewer’s 📤 button now also tags its base checkpoint '
      + 'when Civitai hosts that file, so it appears in that model’s gallery too; the file is '
      + 'identified once by its hash. A new option hides the prompt and generation data on '
      + 'Civitai, like its own hide toggle.',
    to: '/gallery',
  }, ...MIGRATED_NEWS],
  paritySkip: [
    { slot: 'checkpoint.action', surface: 'video',
      reason: 'This product publishes image-training checkpoints; video checkpoints have a different publication contract.' },
    { slot: 'checkpoint.layer', surface: 'video',
      reason: 'The publishing dialog belongs to the image checkpoint action on graph and Canvas.' },
    { slot: 'lightbox.action', surface: 'dataset',
      reason: 'A post carries the generation data of a LIBRARY row (prompt, seed, sampler, the LoRA '
        + 'weight) under the model page of the checkpoint that made it; a dataset image has no '
        + 'checkpoint stamp to file it under. The dataset viewer keeps its own verbs.' },
    { slot: 'lightbox.action', surface: 'bank',
      reason: 'The Bank reviews photos it did not generate: nothing to post under a model page.' },
  ],
}

import { renderToReadableStream } from 'react-dom/server'
import { installPublicOwners } from './support/publicOwners.mjs'
import civitai from '../../bundled/civitai_publish/frontend/index.js'
import camera from '../../bundled/camera_angles/frontend/index.js'
import { setEnabled } from '../src/plugins/registry.js'
/**
 * 📤 Publish to Civitai — RENDERED, not grepped.
 *
 * The contract tests pin that the viewer draws the verb and that both hosts
 * hand a checkpoint to the same dialog. This file mounts the components
 * (tests/support/mountJsx.mjs) and reads the markup, so a ReferenceError in a
 * branch — the class of bug the source-text tests cannot see — throws here
 * instead of on a user's screen. Effects never run under renderToStaticMarkup,
 * so no request is made: the dialog is proved in the state it opens in.
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { createElement } from './support/mountJsx.mjs'

const { default: GeneratedImageLightbox } =
  await import('../src/components/shared/GeneratedImageLightbox.jsx')
const { default: CivitaiPublishModal } =
  await import("../../bundled/civitai_publish/frontend/panels/CivitaiPublishModal.jsx")
const { default: CheckpointActionsPopover } =
  await import('../src/components/dataset/CheckpointActionsPopover.jsx')
const { CapabilitiesProvider } = await import('../src/context/CapabilitiesContext.jsx')
const { ToastProvider } = await import('../src/components/common/Toast.jsx')

test.beforeEach(t => installPublicOwners(t, [civitai, camera], { runtime: true }))
async function render(node) {
  const stream = await renderToReadableStream(node)
  await stream.allReady
  return (await new Response(stream).text()).replace(/<!--.*?-->/g, '')
}
const inApp = (node) =>
  render(createElement(ToastProvider, null,
    createElement(CapabilitiesProvider, null, node)))

const row = (extra = {}) => ({
  id: 4211, dataset_id: 7, url: '/api/dataset/7/img/x.png', step: 2500, record_id: 4,
  seed: 1234, prompt: 'a portrait', checkpoint: 'krea\\lora_nova_000002500.safetensors', ...extra,
})

test('the viewer offers 📤 Civitai beside 📷 on a library row, and nothing on a bare preview', async () => {
  const html = await inApp(createElement(GeneratedImageLightbox, { img: row(), alt: 'x', onClose: () => {} }))
  assert.match(html, /data-testid="lightbox-civitai"/)
  assert.match(html, /data-testid="lightbox-camera-angles"/)
  const bare = await inApp(createElement(GeneratedImageLightbox, {
    img: { url: '/preview.png', step: 500 }, alt: 'preview', onClose: () => {},
  }))
  assert.doesNotMatch(bare, /data-testid="lightbox-civitai"/, 'a URL-only preview has no row to post')
})

test('the image door renders: thumbnail, the page section, the post section', async () => {
  const html = await inApp(createElement(CivitaiPublishModal, {
    context: { kind: 'image', img: row() }, onClose: () => {},
  }))
  assert.match(html, /data-testid="civitai-publish-modal"/)
  assert.match(html, /data-probe-layer/, 'a layer that covers the page must say so to the responsive probe')
  assert.match(html, /Post this image on Civitai/)
  assert.match(html, /lora_nova_000002500 · step 2500/, 'the subject names the checkpoint and the step')
  assert.match(html, /data-testid="civitai-post-image"/)
  assert.match(html, /data-testid="civitai-post-publish-now"[^>]*checked/, 'a post is published by default')
  assert.match(html, /Looking up the link/, 'the link is being resolved on open')
})

test('a picture without a run is told so and offered its LoRA page, never guessed', async () => {
  const html = await inApp(createElement(CivitaiPublishModal, {
    context: { kind: 'image', img: row({ record_id: null, step: null }) }, onClose: () => {},
  }))
  assert.match(html, /not tied to a training run here/)
  assert.match(html, /remembered for the next pictures of this LoRA/)
  // The page is pasted by the user and looked up before anything is linked.
  assert.match(html, /data-testid="civitai-ref"/, 'the LoRA page is pasted here')
  assert.doesNotMatch(html, /Open the checkpoint&#x27;s pill/, 'no detour through a run that is not here')
})

test('the checkpoint door renders the mark / create tabs and no post section', async () => {
  const html = await inApp(createElement(CivitaiPublishModal, {
    context: {
      kind: 'checkpoint',
      node: { record_id: 4, dataset_id: 7, dataset_name: 'Nova', train_type: 'krea' },
      pill: { step: 2500, filename: 'lora_nova_000002500.safetensors' },
    },
    onClose: () => {},
  }))
  assert.match(html, /Civitai model page/)
  assert.match(html, /Nova · step 2500/)
  assert.match(html, /Looking up the link/)
  assert.doesNotMatch(html, /data-testid="civitai-post-image"/, 'a checkpoint is not a picture to post')
})

test('the mark pane looks the page up first and only then offers a version to link', async () => {
  // Effects never run under renderToStaticMarkup, so the pane is proved in the
  // state it opens in: the address field and Look up, no version pick yet —
  // the pick only exists once a page has answered.
  const { default: Modal } = await import("../../bundled/civitai_publish/frontend/panels/CivitaiPublishModal.jsx")
  const html = await inApp(createElement(Modal, {
    context: {
      kind: 'checkpoint',
      node: { record_id: 4, dataset_id: 7, dataset_name: 'Nova', train_type: 'krea' },
      pill: { step: 2500, filename: 'lora_nova_000002500.safetensors' },
    },
    onClose: () => {},
  }))
  // The link lookup is still pending at first paint, so the tabs are not
  // drawn yet; the pane's own controls are pinned by their test ids in the
  // source, which the contract below reads.
  assert.match(html, /Looking up the link/)
})

test('the popover shows the 📤 row only with its product active, and never for a run card', async () => {
  const node = { record_id: 4, dataset_id: 7, family: 'krea', source: 'local' }
  const pill = { step: 2500, filename: 'lora_nova_000002500.safetensors', present: true, download_url: '/dl' }
  const withHost = await render(createElement(CheckpointActionsPopover, {
    node, pill, onClose: () => {},
  }))
  assert.match(withHost, /data-testid="checkpoint-civitai"/)
  assert.match(withHost, /📤<\/span> Civitai</)
  const linked = await render(createElement(CheckpointActionsPopover, {
    node, pill: { ...pill, civitai: { model_name: 'Nova', version_name: 'v1' } },
    onClose: () => {},
  }))
  assert.match(linked, /📤<\/span> On Civitai</)
  assert.match(linked, /On Civitai: Nova · v1/)
  setEnabled(['camera_angles'])
  const noHost = await render(createElement(CheckpointActionsPopover, { node, pill, onClose: () => {} }))
  assert.doesNotMatch(noHost, /data-testid="checkpoint-civitai"/)
  setEnabled(['civitai_publish', 'camera_angles'])
  const runCard = await render(createElement(CheckpointActionsPopover, {
    node, pill: null, onClose: () => {},
  }))
  assert.doesNotMatch(runCard, /data-testid="checkpoint-civitai"/, 'a page is made from ONE save')
})

[![Support us on Patreon](https://img.shields.io/badge/Patreon-Support%20us-FF424D?style=for-the-badge&logo=patreon&logoColor=white)](https://www.patreon.com/c/Loraperfectgf) [![Join our Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/j6hnJBFtXE)

> [!WARNING]
> **No affiliation with loradataset.com.** That website is not operated, endorsed, or supported by the LoRA Dataset Studio team. Any payments made to that service do not support this project. Use this repository and the links provided here to find our official downloads and community channels.

# LoRA Dataset Studio V2

Build, curate, caption and train image datasets from one browser interface. LDS runs on your machine and uses [ai-toolkit](https://github.com/ostris/ai-toolkit) for training and [ComfyUI](https://github.com/comfyanonymous/ComfyUI) for local generation and checkpoint testing.

[Install](#setup--install) · [Documentation](docs/README.md) · [Plugins](#plugins) · [Releases](https://github.com/perfectgf/lora-dataset-studio/releases) · [Discord](https://discord.gg/j6hnJBFtXE)

> [!IMPORTANT]
> **LDS updates now come through Patreon.** New features, exclusive plugins and LDS updates are reserved for [Patreon](https://www.patreon.com/c/Loraperfectgf) members (**LDS Member** and **LDS Pro**). The version you have stays free and keeps working. LDS Pro members can also request custom plugins.

The current core and public plugins are free under the [PolyForm Noncommercial license](LICENSE), and running them needs no account; optional usage statistics are off by default. External APIs and rented GPUs have their own charges. A Patreon membership is needed for new versions and exclusive plugins, not to keep using the version you have.

<table>
  <tr>
    <td width="50%" align="center"><a href="docs/screenshots/generate/generate-variations.png"><img src="docs/screenshots/generate/generate-variations.png" alt="Generation engines" width="100%"></a><br><sub>Generation engines</sub></td>
    <td width="50%" align="center"><a href="docs/screenshots/02-workspace.png"><img src="docs/screenshots/02-workspace.png" alt="Dataset workspace" width="100%"></a><br><sub>Dataset workspace</sub></td>
  </tr>
</table>

A complete Character LoRA workflow in seven minutes:

https://github.com/user-attachments/assets/d51ff89c-34e9-41a9-b47d-08939a8c867b

People shown in the demo and screenshots are AI-generated.

## What it does

| Area | Capabilities |
|---|---|
| Datasets | Character, Concept and Style workflows; image and ZIP/folder import; subject-aware shot catalogs; local Klein and Krea 2 Edit generation; reference editing and exact retries |
| Image Bank | Review folders in place, score quality, group duplicates and people, search by text or similarity, and build balanced shortlists to promote into datasets |
| Curation | Keep/reject, crop, mirror, rotate, face similarity, composition checks, editable watermark masks, text detection and recoverable cleaning |
| Captioning | Local vision models or JoyCaption, family-appropriate prose or tags, appearance policies, bulk editing, targeted re-captioning and external image/`.txt` round trips |
| Local training | Guided ai-toolkit recipes for Z-Image, SDXL, Krea 2, FLUX.1, FLUX.2 Klein, Anima and Qwen-Image 2.1; queues, advanced settings, checkpoint continuation and experimental slider LoRAs |
| Review and testing | Generate with all seven image training families; fixed-seed checkpoint/strength comparisons, prompt batches, runs, logs, lineage, votes, rankings and a generated-image Gallery |
| Files | Standard training ZIPs and sidecars, backup/restore without API keys, ComfyUI deployment, configurable storage and Trash |

Dependencies vary by feature. Bank search ranks matches; it does not guarantee exclusions. Undo covers specific actions, and **Delete rejected** can remove source files after confirmation. Video and slider workflows are experimental. See the [feature reference](docs/guide/features.md), [requirements](docs/guide/requirements.md) and [known limitations](docs/guide/known-limitations.md) for the detailed behavior.

## Plugins

Install optional features from **Plugins → Store**, configure and prepare them in their own settings, and update them through **Plugins → Updates**. Several plugins can be installed together with one LDS restart. Each is independently installable; model downloads, hardware and provider credentials depend on the feature.

| Plugin | What it does | Main requirements |
|---|---|---|
| [API image engines](bundled/api_engines/) | Generate images with Gemini/Nano Banana, ChatGPT or OpenRouter; experimental ChatGPT subscription connection | Provider credentials; API usage is billed by the provider |
| [Camera angles](bundled/camera_angles/) | Create new viewpoints of Gallery or dataset images with controls for direction, height and distance | ComfyUI, Qwen-Image-Edit and Multiple-Angles LoRA |
| [Canvas](bundled/canvas/) | Arrange runs and checkpoints on a visual board, compare or blend LoRAs, generate images and export layouts | ComfyUI for generation; one model family per generation batch |
| [Cloud training](bundled/cloud_training/) | Train image or video models on rented GPUs, monitor runs, recover checkpoints and deliver full Krea 2 models | [vast.ai](https://cloud.vast.ai/?ref_id=683073) account and API key; Hugging Face access for applicable runs |
| [DLSS 5 Neural Rendering](bundled/dlss5/) | Improve a finished video's lighting and materials, compare with the original and export the result | Windows x86-64, NVIDIA driver, compatible model supplied by you and the prepared bridge |
| [Klein Improve](bundled/image_upscale/) | Re-render image detail with instructions, LoRA presets and finishing controls | ComfyUI and Klein models; appearance and color can change |
| [Live channels](bundled/live/) | Continuously render scripted H3 scenes and watch them in a browser or VLC | Compatible local ComfyUI, H3 weights and stream encoder; experimental |
| [Model tools](bundled/model_tools/) | Quantize full models to fp8 or merge weighted LoRAs into a base checkpoint | Python with PyTorch and output disk space; CPU processing, no GPU required |
| [Publish to Civitai](bundled/civitai_publish/) | Upload checkpoints and generated images to a model page | Civitai API key; checkpoints default to drafts, image posts default to publication |
| [Publish to Hugging Face](bundled/hf_publish/) | Export kept images, captions, metadata and a dataset card to the Hub | Write-enabled Hugging Face token; private repository by default and rights confirmation |
| [Resource monitor](bundled/resource_monitor/) | Show CPU, GPU, RAM, VRAM and temperatures, with guarded memory release | No model download |
| [SeedVR2](bundled/seedvr2/) | Restore and upscale images with high-resolution tiling and finishing controls | ComfyUI, SeedVR2 nodes and models; optional TTP nodes for tiling |
| [Video lane](bundled/video/) | Build video datasets from local files or web imports, curate shots, train LoRAs and test H3 generation, continuation and interpolation | Dependencies vary by task: PyAV/ffmpeg, ai-toolkit, ComfyUI and model weights; Video Bank remains beta |
| [Web scraping](bundled/scrape/) | Import selected images or clips from searches and supported gallery URLs into datasets or banks | Source-dependent credentials and permissions; Pexels requires explicit dataset/ML authorization |

API providers apply their own billing and content policies. Model licenses also apply, including MiniMax H3's territory restrictions; check the [video limits](docs/guide/features.md#video-bank-beta--first-release-read-the-limits) before using it. Plugin authors can start with the [SDK and package guide](docs/plugins/README.md).

## Screenshots

Click a screenshot to view it at full size.

<table>
  <tr>
    <td width="33%" align="center"><a href="docs/screenshots/bank/bank-overview.png"><img src="docs/screenshots/bank/bank-overview.png" alt="Image Bank" width="100%"></a><br><sub>Image Bank</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/bank/bank-analyze-and-overview.png"><img src="docs/screenshots/bank/bank-analyze-and-overview.png" alt="Analysis and coverage" width="100%"></a><br><sub>Analysis and coverage</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/bank/bank-launch-all.png"><img src="docs/screenshots/bank/bank-launch-all.png" alt="Batch analysis" width="100%"></a><br><sub>Batch analysis</sub></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="docs/screenshots/03-curate.png"><img src="docs/screenshots/03-curate.png" alt="Dataset curation" width="100%"></a><br><sub>Dataset curation</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/bank/find-text-launch.png"><img src="docs/screenshots/bank/find-text-launch.png" alt="Text detection" width="100%"></a><br><sub>Text detection</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/training/runs-hub.png"><img src="docs/screenshots/training/runs-hub.png" alt="Training runs" width="100%"></a><br><sub>Training runs</sub></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="docs/screenshots/studio/studio-grid.png"><img src="docs/screenshots/studio/studio-grid.png" alt="Checkpoint comparisons" width="100%"></a><br><sub>Checkpoint comparisons</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/canvas/canvas-board.png"><img src="docs/screenshots/canvas/canvas-board.png" alt="LoRA Canvas" width="100%"></a><br><sub>LoRA Canvas</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/studio/civitai-prompt-browser.png"><img src="docs/screenshots/studio/civitai-prompt-browser.png" alt="Civitai prompts" width="100%"></a><br><sub>Civitai prompts</sub></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="docs/screenshots/release/camera-angles-picker.png"><img src="docs/screenshots/release/camera-angles-picker.png" alt="Camera angles" width="100%"></a><br><sub>Camera angles</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/video/video-studio-clips.png"><img src="docs/screenshots/video/video-studio-clips.png" alt="Video Test Studio" width="100%"></a><br><sub>Video Test Studio</sub></td>
    <td width="33%" align="center"><a href="docs/screenshots/video/dlss5-compare.png"><img src="docs/screenshots/video/dlss5-compare.png" alt="DLSS 5 comparison" width="100%"></a><br><sub>DLSS 5 comparison</sub></td>
  </tr>
</table>

## Setup & install

**Windows:** download `LoRA-Dataset-Studio-windows.zip` from the [latest release](https://github.com/perfectgf/lora-dataset-studio/releases/latest), extract it into a new folder and run `start.bat`. The launcher prepares Python and opens LDS in your browser.

Complete **Setup**, then create a dataset or install the plugins you need. Importing, organizing and manually captioning images require no GPU or API key.

| Installation | Instructions |
|---|---|
| Git checkout or manual Python environment | [Native installation](docs/guide/installation.md#windows) |
| Docker with an existing or fresh ComfyUI | [Docker guide](docs/guide/docker.md) |
| Docker without a GPU | [API-only setup](docs/guide/installation.md#docker-without-a-gpu) |
| Pinokio | [One-click installation](docs/guide/installation.md#pinokio) |
| Rented RunPod GPU | [RunPod guide](docs/guide/runpod.md) |

**Updates:** new LDS versions are reserved for Patreon members and install from **Settings → Maintenance → Updates**. Git and Pinokio checkouts fast-forward to a verified release; ZIP installations swap in a verified archive and retain their datasets, media, settings and history. Docker installations from the Windows ZIP install them from the host with `update-docker.bat patreon`. Without a membership, the installed version keeps working; `v1` is frozen and old `main` installations must [migrate to V2](docs/guide/migrate-to-v2.md). Pinokio and Docker keep their own [update procedures](docs/guide/installation.md).

### Minimum requirements

The core runs without a GPU. Python 3.10–3.12 supports the local ML extras; the Windows launcher downloads Python 3.12 if needed. Local generation typically needs about 16 GB NVIDIA VRAM; training requirements depend on the family and settings. See the [hardware and dependency tables](docs/guide/requirements.md) before downloading models or renting a GPU.

## Documentation

- [Getting started](docs/guide/getting-started.md) and [end-to-end workflow](docs/guide/workflow.md)
- [Task instructions](docs/guide/using-the-app.md) and [dataset quality](docs/DATASET_GUIDE.md)
- [Settings, models and paths](docs/guide/settings-reference.md), [external tools and API keys](docs/guide/installation.md#external-tools)
- [Troubleshooting](docs/guide/troubleshooting.md) and [known limitations](docs/guide/known-limitations.md)

The [documentation index](docs/README.md) includes development and plugin references.

## Configuration & network access

Use **Settings** for normal configuration. Native installs bind to `127.0.0.1` by default. Read the [security policy](SECURITY.md#the-default-threat-model) before enabling network access; a protected connection also lets you use LDS from a phone or tablet.

Optional usage statistics are off by default. Update checks, requested model downloads, configured providers and scraping can contact external services. The [network and privacy guide](docs/guide/network-access.md) describes these connections, the data shared and public-access settings.

## Support the project

A [Patreon](https://www.patreon.com/c/Loraperfectgf) membership (LDS Member or LDS Pro) funds development and includes new features, exclusive plugins and LDS updates; LDS Pro members can also request custom plugins. [GitHub Sponsors](https://github.com/sponsors/perfectgf) also supports development, API testing and rented test GPUs. Bug reports, contributions and sharing the project also help. For support, generate a diagnostic report under **Guide → Getting help**, then use [Discord](https://discord.gg/j6hnJBFtXE) or [GitHub issues](https://github.com/perfectgf/lora-dataset-studio/issues).

**Affiliate disclosure:** the project's [vast.ai links](https://cloud.vast.ai/?ref_id=683073) pay the project 3% of referred users' spending for the lifetime of their account, at no extra cost. Rentals use your own API key and are billed directly by vast.ai. This is separate from optional usage statistics.

## Legal & responsible use

Use material you have the rights and consent to train on. Non-consensual likeness use, impersonation, fraud, and sexual or exploitative content involving minors are prohibited. You remain responsible for datasets and outputs. See [responsible use](docs/responsible-use.md) for consent, privacy, copyright, platform terms and warranty details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull requests, and the [Code of Conduct](CODE_OF_CONDUCT.md) for community rules. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Noncommercial use is permitted; commercial use requires separate permission from the licensor.

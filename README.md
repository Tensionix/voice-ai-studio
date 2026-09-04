# Audion Voice AI Studio

<!-- audion:release -->
<p align="center">
  <a href="https://audion.dev/downloads/voice-ai-studio"><img alt="Windows" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0b6db8?style=flat-square&logo=windows&logoColor=white"></a>
  <a href="https://github.com/Tensionix/voice-ai-studio/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Tensionix/voice-ai-studio?style=flat-square&label=release&color=e08a63"></a>
  <a href="https://github.com/Tensionix/voice-ai-studio/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/Tensionix/voice-ai-studio/total?style=flat-square&label=downloads&color=5fd08a"></a>
  <a href="https://github.com/Tensionix/voice-ai-studio/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/Tensionix/voice-ai-studio?style=flat-square&color=5fd08a&logo=apache&logoColor=white&cacheSeconds=3600"></a>
</p>

**Version 2.2.3** · 2026-09-05 · 2,461.4 MB

- [Direct download](https://dl.audion.dev/voice-ai-studio/2.2.3/Audion_Voice_AI_Studio_v2.2.3_Full.zip) — unmetered, no rate limits
- [Project page](https://audion.dev/downloads/voice-ai-studio) — every version and how to install

<p align="center"><img src="docs/screenshot.png" alt="The program window" width="560"></p>

`SHA-256: a3eadbb32205d57cf93b3f8b02f23ce1e7dc3fd5f1ab821ece26901ccba4bfcb`

---

An **Audion** tool, published by [Tensionix](https://github.com/Tensionix).
<!-- /audion:release -->


[Русский](Docs/README_RU.md) · [User Guide](Docs/USER_GUIDE_EN.md)

**Contents**

- [Why It Exists](#why-it-exists)
- [What Has to Be Installed Separately](#what-has-to-be-installed-separately)
- [Editions](#editions)
- [The Principle](#the-principle)
- [What It Can Do](#what-it-can-do)
- [Next](#next)
- [Technical Reference](#technical-reference)
  - [FFmpeg and the NVIDIA Driver](#ffmpeg-and-the-nvidia-driver)
  - [Portability](#portability)

Transcribing recordings, live dictation, cleaning up text, and exporting working
notes. The extended edition — for a workstation with an NVIDIA card.

## Why It Exists

Transcription is needed in two entirely different situations.

**Quickly, right now.** Dictate a paragraph, transcribe a ten-minute recording,
get the text immediately. Response time is what matters, and a cloud service fits
best.

**In bulk, offline.** An hour-long meeting, a whole day of dictaphone recording, a
folder of files. Here autonomy matters more: the recording should not leave the
machine, and the cost should not grow with every hour.

This edition leans on the second. On a machine with a graphics card, local
engines stop being a compromise: an hour of recording is processed in minutes, and
the recording never leaves the computer.

## What Has to Be Installed Separately

**Model weights are not part of the distribution** — about 7 GB: the GigaAM cache
and whisper.cpp with the Turbo and Large V2 models; with the speaker separation
layer (NVIDIA only) about 10.6 GB.

The weights carry their own licences, separate from the program's, and the terms
for speaker separation are accepted **personally, under your own account** — so
the user downloads them.

Until the models are installed, transcription will not start: the window opens,
but there is nothing to recognise with.

**On the first start the app offers to download what is missing.** The
"Download models and engines" window lists the modules with their download
size: GigaAM, whisper.cpp CUDA with the Turbo model, the Large V2 model and,
on an NVIDIA machine, the speaker-separation layer; about 10.6 GB together.
"Download and install" installs them one after another; progress is shown on
the Maintenance page, and once everything is in place every mode there is
green. "Later" postpones the question until the next start, "Don't ask again"
hides the window for good. Modules can still be installed by hand on the same
page.

## Editions

| edition | for what | engines |
|---|---|---|
| Live | laptop, everyday work | cloud services, GigaAM, whisper.cpp |
| **Studio** | workstation with an NVIDIA card | the same plus CUDA and speaker separation |

Live remains the main version for ordinary work. Studio adds accelerated local
engines, large models, and speaker separation.

## The Principle

**Reliable first, elegant second.** Batch transcription behaves predictably, and
everything else rests on it. An overlay on top of other windows and instant
on-the-fly parsing were both tried — and both broke the window itself. Heavy work
is not hung on the thread that draws the interface.

## What It Can Do

Transcribing files and folders, live dictation, separating a recording by
speaker, cleaning up transcribed text,
exporting into working notes, a tray icon for quick access, resetting the
application to its initial state.

## Next

* [User Guide](Docs/USER_GUIDE_EN.md) — step by step, engines, formats.

---

## Technical Reference

### FFmpeg and the NVIDIA Driver

Every FFmpeg build is compiled against a particular version of the
hardware-encoding headers, and each demands its own minimum driver. The newest
build on an old driver does not accelerate anything — it breaks the hardware path.
So the build is chosen to match the driver, not by version number.

### Portability

Application state lives in the project folder, not in the system. A separate
command returns the application to its initial state without touching the
downloaded models.

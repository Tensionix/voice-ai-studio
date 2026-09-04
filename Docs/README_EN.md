# Audion Voice AI Studio

[Русский](README_RU.md) · [User Guide](USER_GUIDE_EN.md)

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

**Model weights are not part of the distribution** — together they come to about
eight gigabytes: the GigaAM cache, whisper.cpp with its large models, and the
speaker separation layer.

The weights carry their own licences, separate from the program's, and the terms
for speaker separation are accepted **personally, under your own account** — so
the user downloads them.

Until the models are installed, transcription will not start: the window opens,
but there is nothing to recognise with.

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

* [User Guide](USER_GUIDE_EN.md) — step by step, engines, formats.

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

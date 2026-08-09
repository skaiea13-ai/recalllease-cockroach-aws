# Narration provenance

- Generator: local, offline Qwen3-TTS through the shared Bounty narration runtime
- Model: `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit`
- Pinned revision: `1c6c0ff58c43afa8df571facde2efa077efd85e2`
- License: Apache-2.0
- Speaker: `Aiden`
- Seed: `20260809`
- Style: low, steady, unhurried documentary delivery with restrained warmth,
  natural breath pauses, longer sentence-final pauses, and no sales tone
- Synthetic speech disclosure: narration generated locally with Qwen3-TTS
  Aiden; no cloned voice or external speech service was used

The five scene-sized takes are normalized independently and joined with 1.0 to
2.0 seconds of deliberate scene spacing. The review mix is mono AAC at 48 kHz,
103.700 seconds long, -16.4 LUFS integrated, 3.6 LU loudness range, and -1.5
dBFS true peak.

Review mix SHA-256:
`4ce95f50d94411a4afd394ae08116cd07c8b7f5291821dc94422c281df8e81d7`

A local Whisper base-model reverse transcription recovered every sentence with
only minor ASR wording and punctuation differences. It correctly recognized
`Recall lease`, `Cockroach DB`, `AWS Lambda`, and `S3`. The spoken source
separates `Recall Lease` for pronunciation clarity; the product remains styled
`RecallLease` on screen and in written copy.

Rebuild with:

```bash
./submission/video/build-narration.sh
```

Generated WAV, AAC, receipt, checksum, and transcription files stay under the
ignored `output/narration/` directory and are not part of the public source
package.

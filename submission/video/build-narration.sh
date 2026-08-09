#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_dir="$(cd "$script_dir/../.." && pwd)"
output_dir="$repo_dir/output/narration"
tts_bin="${BOUNTY_TTS_BIN:-$repo_dir/../tts-narration/bin/bounty-tts}"
tts_seed="${BOUNTY_TTS_SEED:-20260809}"
tts_instruct="${BOUNTY_TTS_INSTRUCT:-Calm documentary narrator. Low, steady, and unhurried, with restrained warmth. Use natural breath-sized pauses and slightly longer sentence-final pauses. Avoid sales energy, dramatic emphasis, and upward inflection. Clearly articulate CockroachDB, AWS Lambda, S3, and SHA-256.}"

for command_name in ffmpeg ffprobe jq shasum; do
  command -v "$command_name" >/dev/null
done
if [[ ! -x "$tts_bin" ]]; then
  echo "Missing executable Bounty TTS runtime: $tts_bin" >&2
  exit 1
fi
"$tts_bin" --check >/dev/null

mkdir -p "$output_dir"
scenes=(01-problem 02-lease 03-revocation 04-retrieval 05-proof-and-close)

for scene in "${scenes[@]}"; do
  source_text="$script_dir/$scene.txt"
  raw_audio="$output_dir/$scene-raw.wav"
  normalized_audio="$output_dir/$scene.wav"
  receipt="$output_dir/$scene.json"
  generation_log="$output_dir/$scene-generation.log"
  if [[ ! -s "$source_text" ]]; then
    echo "Missing narration source: $source_text" >&2
    exit 1
  fi
  "$tts_bin" \
    --file "$source_text" \
    --speaker Aiden \
    --language English \
    --instruct "$tts_instruct" \
    --seed "$tts_seed" \
    --output "$raw_audio" >"$generation_log"
  sed -n '/^{/,$p' "$generation_log" >"$receipt"
  jq -e '.status == "ok" and .speaker == "Aiden"' "$receipt" >/dev/null
  ffmpeg -hide_banner -loglevel error -y \
    -i "$raw_audio" \
    -af "loudnorm=I=-16:LRA=7:TP=-1.5" \
    -c:a pcm_s16le -ar 48000 -ac 1 "$normalized_audio"
done

ffmpeg -hide_banner -loglevel error -y \
  -i "$output_dir/01-problem.wav" \
  -i "$output_dir/02-lease.wav" \
  -i "$output_dir/03-revocation.wav" \
  -i "$output_dir/04-retrieval.wav" \
  -i "$output_dir/05-proof-and-close.wav" \
  -filter_complex "\
    [0:a]apad=pad_dur=1.5[a0];\
    [1:a]apad=pad_dur=1.5[a1];\
    [2:a]apad=pad_dur=2.0[a2];\
    [3:a]apad=pad_dur=1.5[a3];\
    [4:a]apad=pad_dur=1.0[a4];\
    [a0][a1][a2][a3][a4]concat=n=5:v=0:a=1,\
    loudnorm=I=-16:LRA=7:TP=-1.5[a]" \
  -map "[a]" -c:a aac -b:a 192k -ar 48000 -ac 1 \
  "$output_dir/recalllease-aiden-calm.m4a"

ffprobe -v error \
  -show_entries format=duration,size:stream=index,codec_name,codec_type,sample_rate,channels \
  -of json "$output_dir/recalllease-aiden-calm.m4a" \
  >"$output_dir/ffprobe.json"
(
  cd "$output_dir"
  shasum -a 256 ./*.json ./*.m4a ./*.wav >SHA256SUMS
)
cat "$output_dir/ffprobe.json"

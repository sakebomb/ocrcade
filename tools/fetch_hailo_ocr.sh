#!/usr/bin/env bash
# Download the Hailo-10H OCR models the `hailo-ocr` reader uses (config.yaml vlm.hailo_ocr)
# and check each against a pinned sha256. Run on the Pi; safe to re-run.
#
#   tools/fetch_hailo_ocr.sh [dest]        # default dest: ~/hailo-models
set -euo pipefail

dest="${1:-$HOME/hailo-models}"
zoo="https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v5.5.0/hailo10h"
# PP-OCRv6 recognizer's 18710-class key file (blank + 18708 chars + space), Apache-2.0.
# It matches the character_dict in the PP-OCRv6 inference.yml; pinned to one revision.
keys="https://huggingface.co/ruisv/bcdl-ppocr-v6/resolve/708eb80ec53be9b2739b07fe5eaaf76c972f8446/ppocr_keys_v6_18710.txt"

files=(
  "paddle_ocr_v5_mobile_detection.hef|$zoo/paddle_ocr_v5_mobile_detection.hef|cf85836f2612e98d88e8212f6050e34b268f9b2187cec07ce959ca0d94320ad7"
  "paddle_ocr_v6_medium_recognition.hef|$zoo/paddle_ocr_v6_medium_recognition.hef|edfca0ebfa21a921b5dfe5c9145f05a06118cdb9fc4d4ef0eca62fdba58832c8"
  "ppocr_keys_v6_18710.txt|$keys|f950725a97cb6b1a6636d16784aa05720517686581fa8b680ed04f1530a6ab90"
)

mkdir -p "$dest"
for entry in "${files[@]}"; do
  IFS='|' read -r name url sum <<<"$entry"
  path="$dest/$name"
  if [[ -f "$path" ]] && echo "$sum  $path" | sha256sum -c --quiet - 2>/dev/null; then
    echo "ok       $name"
    continue
  fi
  curl -fsSL -o "$path.part" "$url"
  if ! echo "$sum  $path.part" | sha256sum -c --quiet -; then
    rm -f "$path.part"
    echo "checksum mismatch: $name (from $url)" >&2
    exit 1
  fi
  mv "$path.part" "$path"
  echo "fetched  $name"
done

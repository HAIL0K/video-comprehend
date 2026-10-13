"""One-shot transcription helper for re-running a cached video.

Historically CPU-only (the name is kept so existing calls keep working); it now
uses the GPU when ctranslate2 can load the CUDA runtime, and falls back to CPU
int8 otherwise. Usage: python -m scripts.transcribe_cpu <url-or-id> [model]
"""
import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel

from scripts._lib import resolve_source, cache_dir_for, pick_whisper_device
from scripts.transcribe_audio import segments_to_vtt


def main():
    url = sys.argv[1]
    model_name = sys.argv[2] if len(sys.argv) > 2 else "small"
    source = resolve_source(url)
    video_id = source.id
    cache = cache_dir_for(video_id)
    audio_path = cache / "audio.m4a"
    captions_path = cache / "captions.vtt"
    meta_path = cache / "meta.json"

    if not audio_path.exists():
        print(f"audio not found at {audio_path}", file=sys.stderr)
        return 1

    device, compute_type = pick_whisper_device()
    print(f"transcribing {audio_path.name} with {model_name} on {device}/{compute_type}", file=sys.stderr)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(audio_path))
    vtt = segments_to_vtt(segments)
    captions_path.write_text(vtt, encoding="utf-8")

    # Write/patch meta.json
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema_version"] = 3
    meta["transcript_source"] = "whisper"
    meta["whisper_model"] = model_name
    meta["whisper_device"] = device
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(json.dumps({
        "video_id": video_id,
        "meta_path": str(meta_path),
        "captions_path": str(captions_path),
        "transcript_source": "whisper",
        "whisper_model": model_name,
        "whisper_device": device,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

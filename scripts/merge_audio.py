#!/usr/bin/env python3
"""
Lay the narration over the demo and reconcile the two lengths.

    python scripts/merge_audio.py [audio] [--out FILE]

The video was cut to the transcript at 150 wpm, so a recording read at that pace
lands close. It will not land exactly, and the failure mode that matters is
narration being clipped — so the video is fitted to the AUDIO, never the reverse:

  audio longer   the closing frame is held until the voice finishes
  audio shorter  the closing frame is trimmed, but never below three seconds,
                 so the repository URL stays readable

Audio is re-encoded to AAC 192k, which every player and upload form accepts.
"""
import argparse, os, subprocess, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB = os.path.join(ROOT, "submission")
VIDEO = os.path.join(SUB, "KAIROS-prototype-demo.mp4")
DEFAULT_OUT = os.path.join(SUB, "KAIROS-prototype-demo-narrated.mp4")
CANDIDATES = ["voiceover.mp3", "voiceover.m4a", "voiceover.wav",
              "narration.mp3", "audio.mp3"]


def probe(path, stream="v"):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
         "format=duration", "-of", "json", path],
        capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="?")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--video", default=VIDEO)
    a = ap.parse_args()

    audio = a.audio
    if not audio:
        for c in CANDIDATES:
            p = os.path.join(SUB, c)
            if os.path.exists(p):
                audio = p
                break
    if not audio or not os.path.exists(audio):
        sys.exit("no narration found — save it as submission/voiceover.mp3, "
                 "or pass the path as the first argument")
    if not os.path.exists(a.video):
        sys.exit("no video at %s" % a.video)

    v, s = probe(a.video), probe(audio)
    delta = s - v
    print("video     %6.1f s" % v)
    print("narration %6.1f s   (%+.1f s)" % (s, delta))

    if delta > 0.4:
        # hold the last frame so no narration is clipped
        pad = delta + 0.6
        vf = "tpad=stop_mode=clone:stop_duration=%.2f" % pad
        print("→ holding the closing frame %.1f s so the voice finishes" % pad)
        dur = s + 0.6
    elif delta < -0.4:
        trim = min(-delta, max(0.0, v - 3.0))
        dur = v - trim
        vf = None
        print("→ trimming %.1f s of the closing frame" % trim)
    else:
        vf = None
        dur = max(v, s)
        print("→ lengths already match")

    cmd = ["ffmpeg", "-y", "-i", a.video, "-i", audio]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", "%.2f" % dur, "-movflags", "+faststart", a.out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-1500:])
        sys.exit("ffmpeg failed")

    out_v = probe(a.out)
    has_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", a.out],
        capture_output=True, text=True).stdout.strip()
    print("\nwrote %s" % a.out)
    print("  %d:%02d · audio %s · %.1f MB"
          % (out_v // 60, out_v % 60, has_audio or "MISSING",
             os.path.getsize(a.out) / 1048576))


if __name__ == "__main__":
    main()

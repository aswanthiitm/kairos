#!/usr/bin/env python3
"""
Fit the demo video to a narration track.

Uniformly scaling every scene to the audio length keeps the two in proportion,
but it only stays in sync if the reader distributed their time exactly as the
transcript's word counts predict. They never do.

So this measures where the reader ACTUALLY paused — from the audio envelope,
not ffmpeg's silencedetect, which misses a low noise floor — and snaps each
scene boundary to the nearest real pause. Boundaries with no pause nearby fall
back to their proportional position. The result is true sync wherever the reader
gave us a seam, and a sensible estimate everywhere else.

    python scripts/sync_narration.py "submission/voice over.mp3"
"""
import argparse, json, os, subprocess, sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB = os.path.join(ROOT, "submission")

# scene holds as authored, from the transcript at 150 wpm
SCENES = [14.1, 18.5, 14.9, 20.5, 10.9, 19.3, 17.7, 19.7, 11.7, 15.7, 14.9, 11.3]
# frames per scene (the estate scene cycles four ticker frames, entitlements two)
FRAMES_PER_SCENE = [1, 4, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1]
SNAP_WINDOW = 4.5          # seconds either side of a boundary to look for a pause
MIN_SCENE = 4.0            # never squeeze a scene below this


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", path], capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def pauses(path, min_len=0.4):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    sr, win = 8000, 800                                   # 100 ms frames
    n = len(a) // win
    env = np.sqrt((a[:n * win].reshape(n, win) ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(env, 1e-7))
    thr = np.percentile(db, 5) + 6                        # 6 dB over the noise floor
    quiet, out, start = db < thr, [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) / 10.0 >= min_len:
                out.append(((start + i) / 20.0, (i - start) / 10.0))   # (centre, length)
            start = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--frames", default=None, help="directory of f001.png…")
    ap.add_argument("--out", default=os.path.join(SUB, "KAIROS-prototype-demo.mp4"))
    a = ap.parse_args()

    total = duration(a.audio)
    authored = sum(SCENES)
    scale = total / authored
    print("narration %.1f s · authored %.1f s · scale %.4f" % (total, authored, scale))

    # proportional boundaries, then snapped to real pauses
    prop, acc = [], 0.0
    for d in SCENES[:-1]:
        acc += d * scale
        prop.append(acc)
    pz = pauses(a.audio)
    print("pauses detected: %d" % len(pz))

    snapped, used, hits = [], set(), 0
    for i, b in enumerate(prop):
        best, bestd = None, SNAP_WINDOW
        for j, (c, ln) in enumerate(pz):
            if j in used:
                continue
            d = abs(c - b)
            if d < bestd:
                best, bestd = j, d
        if best is not None:
            used.add(best)
            snapped.append(pz[best][0])
            hits += 1
        else:
            snapped.append(b)

    # keep boundaries ordered and scenes above the floor
    for i in range(len(snapped)):
        lo = (snapped[i - 1] if i else 0.0) + MIN_SCENE
        hi = total - MIN_SCENE * (len(snapped) - i)
        snapped[i] = min(max(snapped[i], lo), hi)

    holds = [snapped[0]] + [snapped[i] - snapped[i - 1] for i in range(1, len(snapped))]
    holds.append(total - snapped[-1])
    print("boundaries snapped to a real pause: %d of %d" % (hits, len(prop)))
    for i, h in enumerate(holds):
        mark = "  ←snapped" if i < len(prop) and abs(snapped[i] - prop[i]) > 0.15 else ""
        print("  scene %2d  %5.1f s%s" % (i + 1, h, mark))

    # frames, expanded per scene
    fdir = a.frames or os.path.join(os.environ.get("SP", ""), "nc")
    files = sorted(f for f in os.listdir(fdir) if f.endswith(".png"))
    assert len(files) == sum(FRAMES_PER_SCENE), \
        "expected %d frames, found %d" % (sum(FRAMES_PER_SCENE), len(files))
    plan, k = [], 0
    for si, cnt in enumerate(FRAMES_PER_SCENE):
        per = holds[si] / cnt
        for _ in range(cnt):
            plan.append((os.path.join(fdir, files[k]), per)); k += 1

    XF = 0.45
    inputs = []
    for pth, d in plan:
        inputs += ["-loop", "1", "-t", "%.3f" % (d + XF), "-i", pth]
    fc = ["[%d:v]scale=1600:1000:force_original_aspect_ratio=decrease,"
          "pad=1600:1000:(ow-iw)/2:(oh-ih)/2:color=0xE9E7E2,setsar=1,fps=30[v%d]" % (i, i)
          for i in range(len(plan))]
    prev, off = "[v0]", plan[0][1]
    for i in range(1, len(plan)):
        o = "[x%d]" % i
        fc.append("%s[v%d]xfade=transition=fade:duration=%.2f:offset=%.3f%s"
                  % (prev, i, XF, off, o))
        prev, off = o, off + plan[i][1]

    cmd = (["ffmpeg", "-y"] + inputs + ["-i", a.audio,
           "-filter_complex", ";".join(fc), "-map", prev, "-map", "%d:a:0" % len(plan),
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-t", "%.2f" % total, "-movflags", "+faststart", a.out])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-1500:]); sys.exit("ffmpeg failed")

    d = duration(a.out)
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", a.out],
                           capture_output=True, text=True).stdout.strip()
    print("\nwrote %s\n  %d:%02d · audio %s · %.1f MB"
          % (a.out, d // 60, d % 60, codec or "MISSING", os.path.getsize(a.out) / 1048576))


if __name__ == "__main__":
    main()

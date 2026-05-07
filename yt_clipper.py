#!/usr/bin/env python3
"""
YouTube Video Clipper
=====================
Inspired by the Steve Yegge / Gene Kim pair programming session (Sept 2024)
described in "Vibe Coding" (IT Revolution, 2025).

Usage:
  python yt_clipper.py <youtube_url> [options]

Single clip:
  python yt_clipper.py "https://youtu.be/abc" --start 1:30 --end 2:45

Multiple clips joined with crossfade:
  python yt_clipper.py "https://youtu.be/abc" --start 1:00 3:30 8:10 --end 1:45 4:15 9:00

Keyword search:
  python yt_clipper.py "https://youtu.be/abc" --keyword "vibe coding" --subtitle-style tarantino

List transcript:
  python yt_clipper.py "https://youtu.be/abc" --list-transcript
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ─── Dependency check ────────────────────────────────────────────────────────

REQUIRED_PACKAGES = {
    "yt_dlp": "yt-dlp",
    "youtube_transcript_api": "youtube-transcript-api",
}


def _check_libass():
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    if "libass" not in (r.stdout + r.stderr):
        print("=" * 60)
        print("[WARNING] Your ffmpeg is missing libass!")
        print("  Subtitles will NOT be burned in without it.")
        print("  macOS:          brew install ffmpeg-full")
        print("  Ubuntu/Debian:  sudo apt install ffmpeg")
        print("=" * 60 + "\n")


def check_dependencies():
    missing = []
    for module, pkg in REQUIRED_PACKAGES.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[ERROR] Missing packages: {', '.join(missing)}")
        print(f"  pip install {' '.join(missing)}")
        sys.exit(1)
    if not shutil.which("ffmpeg"):
        print("[ERROR] ffmpeg not found. Install: brew install ffmpeg-full")
        sys.exit(1)
    _check_libass()


check_dependencies()

import yt_dlp  # noqa: E402
from youtube_transcript_api import YouTubeTranscriptApi  # noqa: E402

# ─── Data structures ─────────────────────────────────────────────────────────


@dataclass
class Segment:
    text: str
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def start_ts(self) -> str:
        return seconds_to_ts(self.start)

    @property
    def end_ts(self) -> str:
        return seconds_to_ts(self.end)

    def __str__(self) -> str:
        return f"[{self.start_ts} → {self.end_ts}]  {self.text}"


@dataclass
class ClipSpec:
    """One clip window within the source video."""
    start: float
    end: float
    title: str = "clip"
    segments: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


# ─── Helpers ─────────────────────────────────────────────────────────────────


def seconds_to_ts(s: float) -> str:
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def ts_to_seconds(ts: str) -> float:
    parts = [float(p) for p in ts.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Cannot parse timestamp: {ts!r}")


def extract_video_id(url: str) -> str:
    for pat in [r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", r"(?:embed/)([A-Za-z0-9_-]{11})"]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Cannot extract video ID from: {url}")


def safe_filename(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip()
    return re.sub(r"[\s-]+", "_", s)[:max_len]


# ─── Transcript ───────────────────────────────────────────────────────────────


def fetch_transcript(video_id: str) -> list:
    """Fetch transcript using youtube-transcript-api v1.x (instance-based API)."""
    print(f"[INFO] Fetching transcript for: {video_id}")
    api = YouTubeTranscriptApi()
    try:
        # v1.x: instance method, returns FetchedTranscript (iterable of snippet objects)
        raw = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
    except Exception as first_err:
        print(f"[INFO] English transcript not found ({first_err.__class__.__name__}), trying any language…")
        try:
            tl = api.list(video_id)
            # pick the first available transcript
            transcript = next(iter(tl))
            raw = transcript.fetch()
        except Exception as e:
            print(f"[WARNING] Could not fetch any transcript: {e}")
            return []
    try:
        # Snippets in v1.x are objects with .text / .start / .duration attributes
        segs = [Segment(text=s.text, start=s.start, duration=s.duration) for s in raw]
    except AttributeError:
        # Fallback: older versions returned dicts
        segs = [Segment(text=s["text"], start=s["start"], duration=s["duration"]) for s in raw]
    print(f"[INFO] Got {len(segs)} transcript segments.")
    return segs


def search_transcript(segments: list, keyword: str) -> list:
    kw = keyword.lower()
    return [(i, s) for i, s in enumerate(segments) if kw in s.text.lower()]


def segments_in_range(segments: list, start: float, end: float) -> list:
    # Overlap check: include any segment that overlaps [start, end] at all.
    # Previously used strict containment which silently dropped boundary segments.
    return [s for s in segments if s.start < end + 1.0 and s.end > start - 1.0]


# ─── Video info + download ────────────────────────────────────────────────────


def get_video_info(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(url: str, output_dir: Path) -> Path:
    print("[INFO] Downloading video…")
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "quiet": False,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info)
        if not downloaded.endswith(".mp4"):
            downloaded = downloaded.rsplit(".", 1)[0] + ".mp4"
    return Path(downloaded)


# ─── SRT helpers ─────────────────────────────────────────────────────────────


def float_to_srt_ts(s: float) -> str:
    ms = int((s % 1) * 1000)
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def build_srt_for_clip(clip: ClipSpec, time_offset: float = 0.0,
                        uppercase: bool = False) -> list[tuple]:
    """
    Return list of (rel_start, rel_end, text) tuples for a single clip,
    shifted by time_offset (used when joining multiple clips).
    """
    entries = []
    for seg in clip.segments:
        rel_start = max(0.0, seg.start - clip.start) + time_offset
        rel_end   = max(0.0, seg.end   - clip.start) + time_offset
        text = seg.text.replace("\n", " ")
        if uppercase:
            text = text.upper()
        entries.append((rel_start, rel_end, text))
    return entries


def fix_overlaps(entries: list[tuple]) -> list[tuple]:
    """
    Clamp each entry's end time to strictly before the next entry's start.

    YouTube transcripts often have zero-gap or slightly overlapping segments.
    Without this fix, libass renders both the old and new subtitle simultaneously,
    producing the double-subtitle ghost effect seen in the screenshot.

    Also enforces a minimum gap of 20ms between consecutive entries so
    libass has time to clear the previous subtitle before drawing the next.
    """
    MIN_GAP = 0.020  # 20 ms — enough for libass to clear the previous line
    fixed = list(entries)
    for i in range(len(fixed) - 1):
        start_i, end_i, text_i = fixed[i]
        start_next, end_next, text_next = fixed[i + 1]
        max_end = start_next - MIN_GAP
        if end_i > max_end:
            fixed[i] = (start_i, max(start_i + MIN_GAP, max_end), text_i)
    return fixed


def entries_to_srt(entries: list[tuple]) -> str:
    # Fix overlapping timestamps before writing — this prevents the
    # "ghost subtitle" bug where old text lingers while new text appears.
    entries = fix_overlaps(entries)
    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{float_to_srt_ts(start)} --> {float_to_srt_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


# ─── Subtitle styles ─────────────────────────────────────────────────────────

SUBTITLE_STYLES = {
    "default": {
        "force_style": (
            "FontName=Arial"
            ",FontSize=18"
            ",PrimaryColour=&H00FFFFFF"
            ",OutlineColour=&H00000000"
            ",BackColour=&H80000000"
            ",Bold=1,Outline=2,Shadow=1"
            ",BorderStyle=3"
            ",MarginV=30"
        ),
        "uppercase": False,
        "label": "Default (white on dark box)",
    },
    "tarantino": {
        "force_style": (
            "FontName=Impact"
            ",FontSize=28"
            ",PrimaryColour=&H0000FFFF"   # yellow
            ",OutlineColour=&H00000000"
            ",BackColour=&H00000000"
            ",Bold=1,Italic=0"
            ",Outline=4,Shadow=0"
            ",BorderStyle=1"
            ",Spacing=2,MarginV=40"
            ",Alignment=2"
        ),
        "uppercase": True,
        "label": "Tarantino (yellow Impact, ALL CAPS, thick black outline)",
    },
}


# ─── FFmpeg helpers ───────────────────────────────────────────────────────────


def run_ffmpeg(cmd: list, label: str = "ffmpeg"):
    print(f"[DEBUG] {label}: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in result.stderr.splitlines():
        low = line.lower()
        if any(k in low for k in ("subtitle", "libass", "ass", "warn", "error", "filter")):
            print(f"  [ffmpeg] {line}")
    if result.returncode != 0:
        print(f"[ERROR] {label} failed:\n{result.stderr[-2000:]}")
        sys.exit(1)


def extract_segment(source: Path, start: float, duration: float, out: Path):
    """Cut a single segment from the source (no subtitles yet)."""
    run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(source),
        "-ss", str(start),
        "-t", str(duration),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ], label=f"extract {seconds_to_ts(start)}→{seconds_to_ts(start+duration)}")


def crossfade_two(clip_a: Path, clip_b: Path, out: Path,
                  fade_duration: float, dur_a: float):
    """
    Join clip_a and clip_b with an xfade crossfade.
    dur_a is the duration of clip_a (needed to compute the xfade offset).
    """
    offset = max(0.0, dur_a - fade_duration)
    vf = (
        f"[0:v][1:v]xfade=transition=fade:duration={fade_duration}:offset={offset}[v]"
    )
    af = (
        f"[0:a][1:a]acrossfade=d={fade_duration}[a]"
    )
    run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-filter_complex", vf + ";" + af,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ], label="xfade join")


def get_fontsdir() -> str:
    """Return a fontsdir argument for the ffmpeg subtitles filter, platform-aware.

    On macOS, fontconfig often cannot find system fonts, causing libass to
    render nothing.  Pointing it at the system font directories fixes this.
    """
    import os
    import platform
    system = platform.system()
    candidates = []
    if system == "Darwin":
        candidates = [
            "/System/Library/Fonts",
            "/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"),
        ]
    elif system == "Linux":
        candidates = ["/usr/share/fonts", "/usr/local/share/fonts"]
    # Return first directory that actually exists
    for d in candidates:
        if os.path.isdir(d):
            return d
    return ""


def burn_subtitles(source: Path, srt_path: Path, out: Path, style: str):
    """Burn an SRT file into the video using the subtitles filter."""
    cfg = SUBTITLE_STYLES.get(style, SUBTITLE_STYLES["default"])

    # Escape the SRT path for the ffmpeg filter graph.
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")

    # Build filter string.  Include fontsdir so libass can find system fonts
    # on macOS where fontconfig is often misconfigured.
    fontsdir = get_fontsdir()
    if fontsdir:
        fontsdir_escaped = fontsdir.replace("\\", "/").replace(":", "\\:")
        vf = (
            "subtitles=" + srt_escaped
            + ":fontsdir=" + fontsdir_escaped
            + ":force_style='" + cfg["force_style"] + "'"
        )
    else:
        vf = "subtitles=" + srt_escaped + ":force_style='" + cfg["force_style"] + "'"

    print(f"[DEBUG] fontsdir: {fontsdir or '(none)'}")
    run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(source),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "copy",
        str(out),
    ], label="burn subtitles")


# ─── Multi-clip join pipeline ─────────────────────────────────────────────────


def join_clips(
    source: Path,
    clips: list[ClipSpec],
    output_path: Path,
    fade_duration: float = 0.5,
    burn_captions: bool = True,
    subtitle_style: str = "default",
    tmp: Path = Path("/tmp"),
):
    """
    Pipeline:
      1. Extract each clip to a temp file.
      2. Build an SRT for each clip relative to its own timeline (start = 0).
      3. Burn subtitles onto each clip individually.
         Burning per-clip (not onto the joined video) eliminates cross-clip
         subtitle ghosting caused by timestamp drift across xfade boundaries.
      4. Crossfade-join the already-subtitled clips.
    """
    print(f"\n[INFO] Processing {len(clips)} clip(s) with {fade_duration}s crossfade.")
    style_cfg = SUBTITLE_STYLES.get(subtitle_style, SUBTITLE_STYLES["default"])

    # ── Step 1 & 2 & 3: extract → build SRT → burn subtitles, per clip ──────
    subtitled_files = []
    for i, clip in enumerate(clips):
        print(f"\n[INFO] Clip {i+1}/{len(clips)}: "
              f"{seconds_to_ts(clip.start)} → {seconds_to_ts(clip.end)}"
              f"  ({len(clip.segments)} caption segs)")

        raw_path = tmp / f"seg_{i:02d}_raw.mp4"
        extract_segment(source, clip.start, clip.duration, raw_path)

        if burn_captions and clip.segments:
            # Build SRT with timestamps relative to this clip's own start (offset=0).
            # This is the key fix: each clip's SRT starts at 0s, so there is
            # zero cross-clip timing coupling and no ghost subtitles at joins.
            entries = build_srt_for_clip(clip, time_offset=0.0,
                                          uppercase=style_cfg["uppercase"])
            srt_content = entries_to_srt(entries)
            srt_path = tmp / f"seg_{i:02d}.srt"
            srt_path.write_text(srt_content, encoding="utf-8")
            print(f"       SRT: {len(entries)} entries (first: {srt_content[:80].strip()})")

            subtitled_path = tmp / f"seg_{i:02d}_sub.mp4"
            burn_subtitles(raw_path, srt_path, subtitled_path, subtitle_style)
            subtitled_files.append(subtitled_path)
        else:
            if burn_captions and not clip.segments:
                print("       [WARNING] No transcript segments — no subtitles for this clip.")
            subtitled_files.append(raw_path)

    # ── Step 4: crossfade-join the already-subtitled clips ───────────────────
    if len(subtitled_files) == 1:
        import shutil as _shutil
        _shutil.copy2(subtitled_files[0], output_path)
    else:
        print(f"\n[INFO] Joining {len(subtitled_files)} subtitled clips with {fade_duration}s crossfade…")
        current = subtitled_files[0]
        current_dur = clips[0].duration

        for i in range(1, len(subtitled_files)):
            out_join = tmp / f"joined_{i:02d}.mp4"
            crossfade_two(current, subtitled_files[i], out_join,
                          fade_duration=fade_duration,
                          dur_a=current_dur)
            current_dur = current_dur + clips[i].duration - fade_duration
            current = out_join

        import shutil as _shutil
        _shutil.copy2(current, output_path)

    return output_path


# ─── Interactive mode ─────────────────────────────────────────────────────────


def interactive_pick_clips(segments: list, video_duration: float) -> list[ClipSpec]:
    print("\n" + "═" * 60)
    print("  YouTube Video Clipper — Interactive Mode")
    print("═" * 60)
    print("\nOptions:")
    print("  1. Search transcript by keyword")
    print("  2. Enter start/end timestamps manually")
    choice = input("\nChoice [1/2]: ").strip()
    if choice == "1" and segments:
        return [_keyword_mode(segments, video_duration)]
    return [_manual_mode(segments, video_duration)]


def _keyword_mode(segments: list, video_duration: float) -> ClipSpec:
    keyword = input("Search keyword: ").strip()
    hits = search_transcript(segments, keyword)
    if not hits:
        print(f"[WARNING] No hits for '{keyword}'. Switching to manual mode.")
        return _manual_mode(segments, video_duration)

    print(f"\n  Found {len(hits)} hit(s) for '{keyword}':\n")
    for rank, (idx, seg) in enumerate(hits[:20], 1):
        ctx = " … ".join(segments[j].text for j in range(max(0, idx-1), min(len(segments)-1, idx+1)+1))
        print(f"  [{rank:2d}] {seg.start_ts}  {ctx[:100]}")

    pick = input(f"\nSelect hit [1–{min(len(hits),20)}] (Enter = first): ").strip()
    pick_idx = (int(pick) - 1) if pick.isdigit() else 0
    _, chosen = hits[pick_idx]

    pad = input("Padding in seconds [default: 15]: ").strip()
    pad = float(pad) if pad else 15.0

    start = max(0.0, chosen.start - pad)
    end = min(video_duration, chosen.end + pad)
    return ClipSpec(start=start, end=end, title=safe_filename(keyword),
                    segments=segments_in_range(segments, start, end))


def _manual_mode(segments: list, video_duration: float) -> ClipSpec:
    print(f"\n  Video duration: {seconds_to_ts(video_duration)}")
    start = ts_to_seconds(input("  Start (e.g. 1:30): ").strip())
    end   = ts_to_seconds(input("  End   (e.g. 2:45): ").strip())
    title = input("  Clip title [default: clip]: ").strip() or "clip"
    return ClipSpec(start=start, end=end, title=safe_filename(title),
                    segments=segments_in_range(segments, start, end))


# ─── Main ─────────────────────────────────────────────────────────────────────


def parse_clips_from_args(starts: list[str], ends: list[str],
                          segments: list) -> list[ClipSpec]:
    """Parse --start a b c --end x y z into a list of ClipSpecs.

    Clips are assembled in strict argument order — earlier timestamps given
    later will appear later in the output. No sorting is applied.
    """
    if len(starts) != len(ends):
        print(f"[ERROR] --start has {len(starts)} value(s) but --end has {len(ends)}. They must match.")
        sys.exit(1)
    clips = []
    print("\n[INFO] Clip order (strict argument order — no sorting applied):")
    for i, (s, e) in enumerate(zip(starts, ends), 1):
        start = ts_to_seconds(s)
        end   = ts_to_seconds(e)
        if end <= start:
            print(f"[ERROR] Clip {i}: end time {e} must be after its start time {s}.")
            sys.exit(1)
        # Fetch caption segments for this specific time window.
        # segments_in_range does NOT sort clips — it only filters the transcript.
        cap_segs = segments_in_range(segments, start, end)
        clips.append(ClipSpec(start=start, end=end, segments=cap_segs))
        print(f"  [{i}] {s} → {e}  ({end-start:.1f}s, {len(cap_segs)} caption segs)")
    return clips


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Video Clipper — cut and join clips with burned-in captions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--start", nargs="+", metavar="TIME",
                        help="Start time(s) mm:ss — repeat for multiple clips, e.g. --start 1:00 3:30")
    parser.add_argument("--end",   nargs="+", metavar="TIME",
                        help="End time(s)   mm:ss — must match number of --start values")
    parser.add_argument("--keyword", help="Auto-clip around first transcript hit")
    parser.add_argument("--padding", type=float, default=15.0,
                        help="Seconds of padding around keyword hit (default: 15)")
    parser.add_argument("--fade", type=float, default=0.5,
                        help="Crossfade duration in seconds between clips (default: 0.5)")
    parser.add_argument(
        "--subtitle-style",
        choices=list(SUBTITLE_STYLES.keys()),
        default="default",
        help="Subtitle style: " + ", ".join(f"{k} — {v['label']}" for k, v in SUBTITLE_STYLES.items()),
    )
    parser.add_argument("--no-captions", action="store_true",
                        help="Skip burning captions")
    parser.add_argument("--list-transcript", action="store_true",
                        help="Print full transcript and exit")
    parser.add_argument("--output-dir", default=".",
                        help="Output folder (default: current dir)")
    parser.add_argument("--output-name", default=None,
                        help="Override output filename (without extension)")
    args = parser.parse_args()

    # ── Metadata ─────────────────────────────────────────────────────────
    video_id = extract_video_id(args.url)
    print(f"\n[INFO] Video ID : {video_id}")
    info = get_video_info(args.url)
    title    = info.get("title", "video")
    duration = float(info.get("duration", 0))
    print(f"[INFO] Title    : {title}")
    print(f"[INFO] Duration : {seconds_to_ts(duration)}")

    segments = fetch_transcript(video_id)

    if args.list_transcript:
        print(f"\n{'─'*60}\n  Transcript ({len(segments)} segments)\n{'─'*60}")
        for seg in segments:
            print(seg)
        sys.exit(0)

    # ── Build clip list ───────────────────────────────────────────────────
    if args.start and args.end:
        clips = parse_clips_from_args(args.start, args.end, segments)

    elif args.keyword:
        hits = search_transcript(segments, args.keyword)
        if not hits:
            print(f"[ERROR] No transcript hits for '{args.keyword}'.")
            sys.exit(1)
        _, chosen = hits[0]
        print(f"[INFO] First hit at {chosen.start_ts}: \"{chosen.text}\"")
        start = max(0.0, chosen.start - args.padding)
        end   = min(duration, chosen.end + args.padding)
        clips = [ClipSpec(start=start, end=end, title=safe_filename(args.keyword),
                          segments=segments_in_range(segments, start, end))]
    else:
        clips = interactive_pick_clips(segments, duration)

    # ── Summary ───────────────────────────────────────────────────────────
    total_raw = sum(c.duration for c in clips)
    fades = (len(clips) - 1) * args.fade
    total_out = total_raw - fades
    if len(clips) > 1:
        print(f"\n[INFO] {len(clips)} clips will be joined in this order:")
        for i, c in enumerate(clips, 1):
            print(f"  [{i}] {seconds_to_ts(c.start)} → {seconds_to_ts(c.end)}"
                  f"  ({c.duration:.1f}s)")
        print(f"  Crossfade: {args.fade}s between each  →  {fades:.1f}s total overlap removed")
        print(f"  Final output duration: ~{total_out:.1f}s")
    else:
        c = clips[0]
        print(f"\n[INFO] 1 clip: {seconds_to_ts(c.start)} → {seconds_to_ts(c.end)} ({c.duration:.1f}s)")

    # ── Output path ───────────────────────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.output_name:
        out_name = args.output_name + ".mp4"
    elif len(clips) == 1:
        c = clips[0]
        out_name = f"{safe_filename(title)}_{c.title}_{int(c.start)}s.mp4"
    else:
        out_name = f"{safe_filename(title)}_joined_{len(clips)}clips.mp4"
    output_path = output_dir / out_name

    # ── Process ───────────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        source = download_video(args.url, tmp)

        join_clips(
            source=source,
            clips=clips,
            output_path=output_path,
            fade_duration=args.fade,
            burn_captions=not args.no_captions,
            subtitle_style=args.subtitle_style,
            tmp=tmp,
        )

    size_mb = output_path.stat().st_size / 1_048_576
    print(f"\n{'═'*60}")
    print("  ✅  Done!")
    print(f"     File  : {output_path}")
    print(f"     Size  : {size_mb:.1f} MB")
    print(f"     Clips : {len(clips)}")
    print(f"     Output: ~{total_out:.1f}s")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()

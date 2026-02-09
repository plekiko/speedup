import argparse
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)

def parse_hms(value: str) -> float:
    """
    Accepts:
      SS
      MM:SS
      HH:MM:SS
    (seconds may be fractional)
    """
    parts = value.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        die(f"Invalid time format: {value}")

    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s

    die(f"Invalid time format: {value}")

def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def atempo_chain(speed: float) -> str:
    if speed <= 0:
        die("Speed must be > 0")

    filters = []
    remaining = speed

    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5

    filters.append(f"atempo={remaining:.6f}".rstrip("0").rstrip("."))
    return ",".join(filters)


def ffprobe_duration_seconds(path: Path) -> float:
    if shutil.which("ffprobe") is None:
        die("ffprobe not found (part of FFmpeg)")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd).decode().strip()
        return float(out)
    except Exception:
        die(f"Failed to read video duration for {path.name}")


def run_ffmpeg_with_progress(cmd: list[str], expected_duration: float) -> float:
    start_time = time.perf_counter()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    last_print = 0.0
    assert proc.stdout is not None

    for line in proc.stdout:
        if "=" not in line:
            continue

        k, v = line.strip().split("=", 1)

        if k == "out_time_ms":
            # ffmpeg sometimes emits out_time_ms=N/A
            if v == "N/A":
                continue
            try:
                out_seconds = int(v) / 1_000_000.0
            except ValueError:
                continue

            elapsed = time.perf_counter() - start_time

            if out_seconds > 0 and expected_duration > 0:
                pct = min(100.0, (out_seconds / expected_duration) * 100.0)
                speed = out_seconds / elapsed if elapsed > 0 else 0
                remaining = (expected_duration - out_seconds) / speed if speed > 0 else 0

                if elapsed - last_print > 0.25 or pct >= 100:
                    last_print = elapsed
                    print(
                        f"\rProgress: {pct:5.1f}% | "
                        f"Elapsed: {fmt_time(elapsed)} | "
                        f"ETA: {fmt_time(remaining)}",
                        end="",
                        flush=True,
                    )

        if k == "progress" and v == "end":
            break

    print()

    rc = proc.wait()
    total = time.perf_counter() - start_time

    if rc != 0:
        err = proc.stderr.read().strip() if proc.stderr else ""
        die(f"ffmpeg failed\n{err}")

    return total


@dataclass(frozen=True)
class FileDirectives:
    should_process: bool
    start: float | None
    end: float | None
    clean_stem: str


_START_RE = re.compile(r"--start=([0-9]+(?:[_:][0-9]+){0,2}(?:\.[0-9]+)?)")
_END_RE   = re.compile(r"--end=([0-9]+(?:[_:][0-9]+){0,2}(?:\.[0-9]+)?)")

def parse_filename_directives(path: Path) -> FileDirectives:
    stem = path.stem

    should_process = "--slow" in stem

    start = None
    end = None

    m = _START_RE.search(stem)
    if m:
        raw = m.group(1).replace("_", ":")
        start = parse_hms(raw)

    m = _END_RE.search(stem)
    if m:
        raw = m.group(1).replace("_", ":")
        end = parse_hms(raw)

    # Clean output name: remove directives from filename
    clean = stem
    clean = clean.replace("--slow", "")
    clean = _START_RE.sub("", clean)
    clean = _END_RE.sub("", clean)

    # normalize leftover separators/spaces
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"__+", "_", clean)
    clean = re.sub(r"--+", "-", clean)
    clean = re.sub(r"_-", "_", clean)
    clean = re.sub(r"-_", "_", clean)
    clean = clean.strip(" _-")

    if not clean:
        clean = "output"

    return FileDirectives(
        should_process=should_process,
        start=start,
        end=end,
        clean_stem=clean,
    )


def build_ffmpeg_cmd(
    input_path: Path,
    output_path: Path,
    speed: float,
    fps_normal: int,
    fps_fast: int,
    no_audio: bool,
    crf: int,
    preset: str,
    start: float | None,
    end: float | None,
) -> tuple[list[str], float]:
    in_dur = ffprobe_duration_seconds(input_path)
    partial = start is not None or end is not None

    base_cmd = [
        "ffmpeg",
        "-y",
        "-fflags", "+genpts",
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
        "-progress", "pipe:1",
        "-i", str(input_path),
    ]

    if partial:
        s = start or 0.0
        e = end if end is not None else in_dur

        if e <= s:
            die(f"{input_path.name}: --end must be greater than --start")

        seg = e - s
        expected = s + (seg / speed) + (in_dur - e)

        vf = (
            f"[0:v]split=3[v0][v1][v2];"
            f"[v0]trim=0:{s},setpts=PTS-STARTPTS,fps={fps_normal}[v0t];"
            # sped part: generate at fps_fast, then convert to fps_normal for concat compatibility
            f"[v1]trim={s}:{e},setpts=(PTS-STARTPTS)/{speed},fps={fps_fast},fps={fps_normal}[v1t];"
            f"[v2]trim={e},setpts=PTS-STARTPTS,fps={fps_normal}[v2t];"
            f"[v0t][v1t][v2t]concat=n=3:v=1:a=0[v]"
        )


        af = None
        if not no_audio:
            sped_out_dur = (e - s) / speed

            af = (
                f"[0:a]asplit=2[a0][a2];"
                f"[a0]atrim=0:{s},asetpts=PTS-STARTPTS[a0t];"
                f"[a2]atrim={e},asetpts=PTS-STARTPTS[a2t];"
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=duration={sped_out_dur},asetpts=PTS-STARTPTS[asil];"
                f"[a0t][asil][a2t]concat=n=3:v=0:a=1[a]"
            )

        cmd = base_cmd + [
            "-filter_complex", vf if af is None else vf + ";" + af,
            "-map", "[v]",
            "-vsync", "cfr",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-movflags", "+faststart",
        ]

        if no_audio:
            cmd += ["-an"]
        else:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]

    else:
        expected = in_dur / speed
        vfilter = f"setpts=PTS/{speed},fps={fps_fast}"

        cmd = base_cmd + [
            "-filter:v", vfilter,
            "-vsync", "cfr",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-movflags", "+faststart",
        ]

        if no_audio:
            cmd += ["-an"]
        else:
            cmd += ["-filter:a", atempo_chain(speed), "-c:a", "aac", "-b:a", "192k"]

    cmd += [str(output_path)]
    return cmd, expected


def process_one(
    input_path: Path,
    output_path: Path,
    args,
    start_override: float | None,
    end_override: float | None,
) -> None:
    print(f"Rendering {input_path.name} to {output_path.name}")

    cmd, expected = build_ffmpeg_cmd(
        input_path=input_path,
        output_path=output_path,
        speed=args.speed,
        fps_normal=args.fps_normal,
        fps_fast=args.fps_fast,
        no_audio=args.no_audio,
        crf=args.crf,
        preset=args.preset,
        start=start_override,
        end=end_override,
    )

    total = run_ffmpeg_with_progress(cmd, expected)
    print(f"Done in {fmt_time(total)}")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Speed up video(s) smoothly with FFmpeg")
    p.add_argument("input", type=Path, help="Input .mp4 file OR a folder containing .mp4 files")
    p.add_argument("output", type=Path, nargs="?", help="Output file (single-file mode). Ignored in folder mode.")
    p.add_argument("--out-dir", type=Path, help="Folder mode: where to write outputs (default: <input>/_processed)")
    p.add_argument("--start", type=parse_hms, help="Single-file mode: start (SS, MM:SS, or HH:MM:SS)")
    p.add_argument("--end", type=parse_hms, help="Single-file mode: end (SS, MM:SS, or HH:MM:SS)")
    p.add_argument("--speed", type=float, required=True)
    p.add_argument("--fps-normal", type=int, default=60, help="FPS for unprocessed parts (default: 60)")
    p.add_argument("--fps-fast", type=int, default=60, help="FPS used while generating the sped-up part (default: 60)")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--preset", default="medium")
    args = p.parse_args()

    if args.speed <= 0:
        die("Speed must be > 0")
    if shutil.which("ffmpeg") is None:
        die("ffmpeg not found")

    if not args.input.exists():
        die("Input path not found")

    # Folder mode
    if args.input.is_dir():
        in_dir = args.input
        out_dir = args.out_dir or (in_dir / "_processed")
        out_dir.mkdir(parents=True, exist_ok=True)

        mp4s = sorted(in_dir.rglob("*.mp4"))

        if not mp4s:
            die(f"No .mp4 files found in folder: {in_dir}")

        processed = 0
        skipped = 0

        for mp4 in mp4s:
            d = parse_filename_directives(mp4)

            if not d.should_process:
                skipped += 1
                continue

            rel_parent = mp4.parent.relative_to(in_dir)  # subfolder path inside input dir
            target_dir = out_dir / rel_parent
            target_dir.mkdir(parents=True, exist_ok=True)

            out_name = f"{d.clean_stem}.mp4"
            out_path = target_dir / out_name

            # If you want to avoid overwriting, uncomment:
            # if out_path.exists():
            #     die(f"Output already exists: {out_path}")

            process_one(
                input_path=mp4,
                output_path=out_path,
                args=args,
                start_override=d.start,
                end_override=d.end,
            )
            processed += 1

        print(f"Folder complete. Processed: {processed} | Skipped: {skipped} | Output: {out_dir}")
        return

    # Single-file mode (backward compatible)
    input_file = args.input
    if not input_file.exists():
        die("Input file not found")

    if args.output is None:
        die("Single-file mode requires an output path argument")

    output_file = args.output
    output_file.parent.mkdir(parents=True, exist_ok=True)

    process_one(
        input_path=input_file,
        output_path=output_file,
        args=args,
        start_override=args.start,
        end_override=args.end,
    )


if __name__ == "__main__":
    main()

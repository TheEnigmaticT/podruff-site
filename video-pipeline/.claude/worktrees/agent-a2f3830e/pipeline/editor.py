import os
import subprocess
import tempfile


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def extract_segment(input_path: str, start: float, end: float, output_path: str) -> None:
    _run_ffmpeg([
        "-ss", str(start),
        "-i", input_path,
        "-to", str(end - start),
        "-c", "copy",
        output_path,
    ])


def extract_frame(input_path: str, timestamp: float, output_path: str) -> None:
    _run_ffmpeg([
        "-ss", str(timestamp),
        "-i", input_path,
        "-frames:v", "1",
        output_path,
    ])


def prepend_hook(hook_path: str, segment_path: str, output_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(f"file '{hook_path}'\nfile '{segment_path}'\n")
        concat_file = f.name
    try:
        _run_ffmpeg([
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path,
        ])
    finally:
        os.unlink(concat_file)


def create_short(input_path: str, output_path: str, max_duration: float = 59.0) -> None:
    _run_ffmpeg([
        "-i", input_path,
        "-t", str(max_duration),
        "-vf", "crop=ih*9/16:ih,scale=1080:1920",
        "-c:a", "copy",
        output_path,
    ])

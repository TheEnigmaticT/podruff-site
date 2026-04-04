#!/usr/bin/env python3
"""Generate branded social media images from prompts using Nano Banana 2.

Usage:
    python3 social_image_gen.py "prompt text" output.png
    python3 social_image_gen.py --batch prompts.json output_dir/

The batch JSON format is a list of objects:
    [{"prompt": "...", "filename": "quote-1.png"}, ...]

Reads GEMINI_API_KEY from environment, social_config.json, or video-pipeline .env.
"""

import json
import os
import sys

from google import genai

MODEL = "gemini-3.1-flash-image-preview"

_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_api_key():
    """Resolve Gemini API key from env, social config, or video-pipeline .env."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key

    # Try social_config.json
    try:
        with open(os.path.join(_DIR, "social_config.json")) as f:
            cfg = json.load(f)
        key = cfg.get("gemini_api_key", "")
        if key:
            return key
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Fallback: video-pipeline .env
    env_path = os.path.expanduser("~/video-pipeline/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1]
    except FileNotFoundError:
        pass

    return ""


def generate_image(prompt, output_path):
    """Generate a single image from a text prompt. Returns the output path on success."""
    api_key = _get_api_key()
    if not api_key:
        print("ERROR: No GEMINI_API_KEY found", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt],
    )

    for part in response.parts:
        if part.inline_data is not None:
            image = part.as_image()
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            image.save(output_path)
            return output_path

    print("ERROR: No image returned by model", file=sys.stderr)
    return None


def generate_batch(prompts_file, output_dir):
    """Generate multiple images from a JSON prompts file.

    Args:
        prompts_file: Path to JSON file with list of {"prompt": ..., "filename": ...}
        output_dir: Directory to save generated images

    Returns:
        List of {"filename": ..., "path": ..., "success": bool}
    """
    with open(prompts_file) as f:
        prompts = json.load(f)

    os.makedirs(output_dir, exist_ok=True)
    results = []

    for item in prompts:
        prompt = item["prompt"]
        filename = item["filename"]
        output_path = os.path.join(output_dir, filename)

        print(f"Generating: {filename}...", file=sys.stderr)
        try:
            result = generate_image(prompt, output_path)
            results.append({
                "filename": filename,
                "path": output_path,
                "success": result is not None,
            })
        except Exception as exc:
            print(f"ERROR generating {filename}: {exc}", file=sys.stderr)
            results.append({
                "filename": filename,
                "path": output_path,
                "success": False,
            })

    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage:", file=sys.stderr)
        print("  python3 social_image_gen.py 'prompt' output.png", file=sys.stderr)
        print("  python3 social_image_gen.py --batch prompts.json output_dir/", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--batch":
        results = generate_batch(sys.argv[2], sys.argv[3])
        succeeded = sum(1 for r in results if r["success"])
        print(json.dumps(results, indent=2))
        print(f"\n{succeeded}/{len(results)} images generated", file=sys.stderr)
    else:
        path = generate_image(sys.argv[1], sys.argv[2])
        if path:
            print(path)
        else:
            sys.exit(1)

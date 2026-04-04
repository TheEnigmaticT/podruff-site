from google import genai
from google.genai import types
from pipeline.config import GEMINI_API_KEY
from pipeline.retry import retry

_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


THUMBNAIL_PROMPT = (
    "Edit this video frame into a professional YouTube thumbnail. "
    "PRESERVE the person's face and likeness exactly. "
    "Add a bold, attention-grabbing 3-5 word headline: \"{headline}\". "
    "Use bright, high-contrast colors and a clean 16:9 layout. "
    "Make the text large and readable at small sizes."
)


@retry(max_attempts=3, exceptions=(Exception,))
def generate_thumbnail(frame_path: str, headline: str, output_path: str) -> None:
    """Generate a YouTube thumbnail using Nano Banana Pro with real frame input."""
    client = _get_client()

    with open(frame_path, "rb") as f:
        frame_bytes = f.read()

    # Determine MIME type from extension
    mime = "image/png" if frame_path.lower().endswith(".png") else "image/jpeg"

    response = client.models.generate_content(
        model="nano-banana-pro-preview",
        contents=[
            types.Part.from_bytes(data=frame_bytes, mime_type=mime),
            THUMBNAIL_PROMPT.format(headline=headline),
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        ),
    )

    # Extract image from response parts
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.mime_type.startswith("image/"):
            with open(output_path, "wb") as f:
                f.write(part.inline_data.data)
            return

    raise RuntimeError("Nano Banana Pro did not return an image")

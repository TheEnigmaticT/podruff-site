"""MLT / Kdenlive project file parser for the video pipeline.

Parses .kdenlive files (MLT XML format) to extract source time ranges
from editor cut points.  Returns the same ``list[tuple[float, float]]``
type as :func:`pipeline.fcp7.parse_fcp7_xml`.
"""

import xml.etree.ElementTree as ET

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def _parse_mlt_time(ts: str) -> float:
    """Parse an MLT timestamp to seconds.

    Handles three forms:
    - ``HH:MM:SS.mmm``  (full form used by Kdenlive)
    - ``MM:SS.mmm``      (short form)
    - ``<number>``       (raw frame count or seconds — returned as float)

    Args:
        ts: Timestamp string from an ``in=`` or ``out=`` attribute.

    Returns:
        Elapsed time in seconds as a float.
    """
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(ts)  # frame count or plain seconds


def parse_kdenlive_mlt(path: str) -> list[tuple[float, float]]:
    """Parse a Kdenlive .kdenlive file and return source time ranges.

    Reads the MLT XML structure, identifies chains that reference video
    files, then finds the first playlist whose entries point to those
    chains.  Returns the ``(start_seconds, end_seconds)`` pairs in
    timeline order.

    Algorithm:
    1. Parse the XML file.
    2. Build ``chain_id -> resource_path`` for all ``<chain>`` elements
       whose ``resource`` property ends with a recognised video extension.
    3. Walk all ``<playlist>`` elements; for each, collect ``<entry>``
       children that have a ``producer=`` attribute referencing a video
       chain.  Pick the *first* playlist that yields at least one such
       entry (it is the primary video track).
    4. Parse each qualifying entry's ``in=`` / ``out=`` timestamps and
       return them as (start, end) tuples.

    Args:
        path: Absolute path to the .kdenlive (MLT XML) file.

    Returns:
        List of ``(start_seconds, end_seconds)`` tuples, one per clip
        segment in the video track, in timeline order.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ET.ParseError: If the file is not valid XML.
    """
    tree = ET.parse(path)
    root = tree.getroot()

    # ------------------------------------------------------------------ #
    # Step 1: Build video-chain set                                        #
    # ------------------------------------------------------------------ #
    video_chain_ids: set[str] = set()

    for chain in root.findall("chain"):
        chain_id = chain.get("id", "")
        if not chain_id:
            continue
        resource_prop = chain.find("property[@name='resource']")
        if resource_prop is None or not resource_prop.text:
            continue
        resource = resource_prop.text.strip()
        # Accept chains whose resource ends with a video extension
        lower_resource = resource.lower()
        if any(lower_resource.endswith(ext) for ext in VIDEO_EXTENSIONS):
            video_chain_ids.add(chain_id)

    # ------------------------------------------------------------------ #
    # Step 2: Find first playlist with video-chain entries                 #
    # ------------------------------------------------------------------ #
    results: list[tuple[float, float]] = []

    for playlist in root.findall("playlist"):
        candidate: list[tuple[float, float]] = []

        for entry in playlist.findall("entry"):
            producer = entry.get("producer", "")
            if not producer:
                continue  # no producer= attribute — skip gap/blank
            if producer not in video_chain_ids:
                continue  # references a non-video chain (audio, black, etc.)

            in_attr = entry.get("in", "")
            out_attr = entry.get("out", "")
            if not in_attr or not out_attr:
                continue

            start_sec = _parse_mlt_time(in_attr)
            end_sec = _parse_mlt_time(out_attr)
            candidate.append((start_sec, end_sec))

        if candidate:
            # Return the first playlist that has valid video entries
            results = candidate
            break

    return results

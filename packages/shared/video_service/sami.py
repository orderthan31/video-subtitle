import math
import re
from xml.etree import ElementTree as ET


def segments_to_sami(segments, language="ko"):
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
        raise ValueError("Invalid SAMI language code")
    events = {0: ""}
    previous_end = 0
    for segment in segments:
        if not (math.isfinite(segment.start) and math.isfinite(segment.end)
                and 0 <= segment.start < segment.end):
            raise ValueError("Invalid SAMI time interval")
        start, end = round(segment.start * 1000), round(segment.end * 1000)
        if start < previous_end or end <= start:
            raise ValueError("Overlapping or submillisecond SAMI cue")
        if not segment.text.strip():
            continue
        events[start] = segment.text
        events[end] = ""
        previous_end = end
    root = ET.Element("SAMI")
    head = ET.SubElement(root, "HEAD")
    ET.SubElement(head, "TITLE").text = "Translated subtitles"
    ET.SubElement(head, "META", {"http-equiv": "Content-Type", "content": "text/html; charset=utf-8"})
    ET.SubElement(head, "STYLE", {"TYPE": "text/css"}).text = (
        f".SUBTTL {{ Name: Subtitles; lang: {language}; SAMIType: CC; }}")
    body = ET.SubElement(root, "BODY")
    for timestamp, text in sorted(events.items()):
        sync = ET.SubElement(body, "SYNC", {"Start": str(timestamp)})
        paragraph = ET.SubElement(sync, "P", {"Class": "SUBTTL"})
        lines = text.splitlines() if text else ["\u00a0"]
        paragraph.text = lines[0]
        for line in lines[1:]:
            ET.SubElement(paragraph, "BR").tail = line
    # SAMI readers recognize the named entity as the explicit caption-clear marker.
    return ET.tostring(root, encoding="unicode", method="html").replace("\u00a0", "&nbsp;") + "\n"

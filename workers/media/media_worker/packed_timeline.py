from video_service.storage import write_json_atomic
from video_service.timeline import project_segment_to_original


def restore_segments(segments, spans, trace):
    restored, crossings = [], []
    for segment in segments:
        pieces = project_segment_to_original(segment.start, segment.end, spans)
        if len(pieces) > 1:
            # Sentence timing cannot identify which words belong to each piece.
            crossings.append({"segment": segment.to_dict(), "original_intervals": pieces,
                              "text_policy": "repeat-full-sentence-on-each-piece"})
        restored.extend(segment.with_times(a, b) for a, b in pieces if round(b * 1000) > round(a * 1000))
    write_json_atomic(trace, crossings)
    return restored

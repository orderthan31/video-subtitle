"""Share adjacent duplicate translations without extending any subtitle interval."""


def translation_groups(segments, crossings=()):
    origins = {}
    for index, crossing in enumerate(crossings):
        text = crossing['segment']['text']
        for start, end in crossing['original_intervals']:
            origins.setdefault((round(start, 6), round(end, 6), text), set()).add(index)

    def source_ids(segment):
        return origins.get((round(segment.start, 6), round(segment.end, 6), segment.text), set())

    groups = []
    for segment in segments:
        if groups and segment.text == groups[-1][-1].text:
            previous = groups[-1][-1]
            same_source = bool(source_ids(previous) & source_ids(segment))
            gap = segment.start - previous.end
            close = -1e-8 <= gap <= .300001 and segment.end - groups[-1][0].start <= 8
            if same_source or close:
                groups[-1].append(segment)
                continue
        groups.append([segment])
    return groups

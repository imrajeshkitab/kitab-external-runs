"""The two steps that can run in-house or externally, behind one interface.

    normalise(job) -> {"chapters": [{"index", "kind", "heading", "text"}]}     narratable -> speakable text
    synthesise(job, out_dir) -> {"audio_path", "ext", "report": {...}}        speakable text -> one audio file

`job` is a dict: id, linear_id, language, title, attempt, narratable (for normalise) or speakable (for synthesise).
The report carries at least duration_s and model; chapters (and sentences) with start/end seconds when known.
"""

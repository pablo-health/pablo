# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drug names as people say them: the transcriber's vocabulary and the sound-alike guard.

Two halves, both deterministic:

- :mod:`.keyterms` builds the transcriber's per-session vocabulary from the
  client's own chart. There is no fixed formulary list: a list holding one
  name of a sound-alike pair makes the transcriber write that name when the
  other one is said, so only what this client's chart names is sent.
- :mod:`.sound_alikes` reads the transcript after the fact. A drug name the
  chart does not list, that sounds like one it does, is kept as heard and
  marked for the clinician, and is never proposed to the chart.

:mod:`.names` holds the generic and brand names shared by both.
"""

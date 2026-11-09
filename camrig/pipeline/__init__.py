"""One module per step. Each defines VERSION, inputs(project, decisions), settings(project, decisions) and
run(project, decisions, out) -> camrig.record.Check. Top-level imports are the standard library and camrig only:
the runner imports every step in the core environment to fingerprint it, so heavy libraries (torch, mediapipe)
are imported inside run()."""

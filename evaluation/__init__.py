"""Reading-accuracy evaluation: labelled samples, metrics, and the readers under test.

`metrics`, `dataset`, `parse` and `solutions` are stdlib-only so they test in CI.
`readers` touches the camera-side backends and imports device libraries lazily.
The workflow is in docs/EVAL.md.
"""

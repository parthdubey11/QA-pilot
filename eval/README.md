# Eval

Benchmarks QA Pilot against `demo-shop` and compares its findings with `demo-shop/GROUND_TRUTH.md`:
recall, precision, time and tokens per run, and QA Pilot vs. axe-core alone for accessibility.

- `run_eval.py`: runs the benchmark through the API (the stack must be running) and writes `results/eval-<date>.json`
  and `.md`. Use `--help` for options, and `--rescore <json>` to re-score saved data without new runs.
- `matching.py`: deterministic rules that map a reported bug or accessibility issue to a planted one.
- `test_matching.py`: tests for the rules (`python -m pytest eval`).

See the *Evaluation* section of the main README for commands and the method.

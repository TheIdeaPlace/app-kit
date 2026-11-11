# theideaplace-appkit

Shared pieces for The Idea Place's wxPython apps. Nothing in it imports wx, so each module can
be tested on its own and used from any app.

## Modules

- `difftext`: a diff as text to read a line at a time with a screen reader. Each line starts
  with its mark, in the style the person chooses: words ("Added:", "Removed:", "Unchanged:"),
  short words ("Add", "Del", "Same"), diff symbols ("+", "-", "="), or diff symbols with
  changed lines, where a removed line and the line that replaces it read "!- old", then
  "!+ new" (each change heard whole, so those pairs come out of the diff's order). Every
  style gives one line out for each line in. `parse_unified` reads `git diff` output and
  GitHub's `files[].patch`; not combined (`@@@`) or coloured diffs, so run git with
  `--no-color`. Used by The Chat Place's Changed Files (TheIdeaPlace/AIChat#174).

- `sounds`: short sounds from `sounds/` at the top of this repo, played one at a time with the
  flood rules in that folder's README. `SoundPlayer.play` returns at once, takes a priority
  (`AMBIENT`, `NORMAL` or `URGENT`), and no audio problem reaches the caller. `stop` is for Mute,
  and `preview` and `stop_preview` are for a settings screen. Windows uses `waveOut`, macOS
  `afplay`, and Linux `pw-play`, `paplay` or `aplay`, so it adds no dependency. Also here:
  `read_wav`, which checks a person's own file, `safe_file_name`, and `quiet_now`, for
  presentations and full-screen programs. Used by The Chat Place (TheIdeaPlace/AIChat#250).

- `soundschemes`: sound schemes and scheme files as `sounds/SCHEMES.md` sets them out. `catalog`
  gives an app's events from `apps.json`; `parse_ref`, `resolve` and `portable` handle a stored
  sound; `read_scheme_file` checks a file from anywhere and `write_scheme_file` makes one the same
  way every time; `install_sounds` puts an imported scheme's sounds in the app's folder.

- `soundgallery`: the sound gallery's signed list and checked downloads. `Gallery(app).schemes()`
  and `.download(entry)`, raising a `GalleryProblem` whose text can be shown. Checking the signature
  needs the `gallery` extra (`pycryptodomex`), or `cryptography`.

## Using it

Pin a tag or a commit of main in the app's `requirements.txt`:

```
theideaplace-appkit @ git+https://github.com/TheIdeaPlace/app-kit@python-v0.2.0#subdirectory=python
```

Import it at the top of a module the app always imports, so PyInstaller bundles it without a
`--hidden-import`.

## Releasing

Bump `version` in `pyproject.toml` and `__version__` in `theideaplace_appkit/__init__.py`
together, merge, then tag `python-v<version>`. Apps move to the new tag on their own schedule.

## Tests

```
python -m pytest python/tests
```

## Test coverage

The **Coverage** workflow runs every Monday at 13:00 UTC (or by hand: Actions, Coverage, Run
workflow). It measures how much of `theideaplace_appkit` the tests run on Windows, macOS and Linux,
lines and branches, with Python 3.12, and combines the three (#41). The tests themselves aren't
measured. `tools/coverage_check.py` compares the result with `testing/coverage/baseline.json` and
flags:

- the whole package falling more than 1 point;
- a file of 50 statements or more falling more than 5 points;
- any file, a new one included, with 25 or more statements more that no test runs.

A drop files an issue labelled `coverage-regression`, or comments on the one already open. When a
new file is flagged, the report also names any file gone since the baseline, as the new one may
only have moved. Each run's summary page shows the report, and its `coverage-report` download has
`coverage.json` and the per-line HTML.

The baseline changes only when it's accepted, so a slow slide is still caught. When a drop is
expected (code moved, tests removed on purpose), or coverage rose and the bar should rise with it,
download `coverage.json` from a run's `coverage-report` and accept it in a pull request, from this
folder:

```
gh run download RUN_ID -n coverage-report -D cov
python tools/coverage_check.py cov/coverage.json --accept --measured "main at SHA, DATE, run RUN_ID"
```

To measure locally: `pip install pytest-cov`, then, from this folder,
`python -m pytest -q tests --cov --cov-report=term-missing` (`pyproject.toml` has the settings).
One platform alone reads lower than the combined figure, because each skips the others' tests.

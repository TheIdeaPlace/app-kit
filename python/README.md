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

## Using it

Pin a tag or a commit of main in the app's `requirements.txt`:

```
theideaplace-appkit @ git+https://github.com/TheIdeaPlace/app-kit@python-v0.1.0#subdirectory=python
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

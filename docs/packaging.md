# Packaging

`pyproject.toml` is the only packaging config. The version has a single
source, `__version__` in `agent_mail.py`; setuptools reads it statically.
The wheel contains `agent_mail.py` and metadata only. The sdist is an explicit
allow-list in `MANIFEST.in`; anything not listed does not ship (apart from the
metadata setuptools itself generates: `PKG-INFO`, `setup.cfg` and
`agent_postbox.egg-info/`).

Build and check, in a throwaway virtualenv (never your project's):

```bash
python -m venv /tmp/pb-build && /tmp/pb-build/bin/python -m pip install build
/tmp/pb-build/bin/python -m build --outdir /tmp/pb-out .
python -m venv /tmp/pb-run && /tmp/pb-run/bin/pip install /tmp/pb-out/*.whl
/tmp/pb-run/bin/agent-postbox --version
```

`python test_packaging.py` automates this (building a copy of the tree, so the
checkout stays clean) and fails if the artifacts contain anything that is not
on the allow-list, or anything resembling a private file. It needs the `build`
package and network access to fetch the build backend; without `build` it
skips with a message.

Releasing (tagging, publishing) is a maintainer decision and is not
automated here; the steps are in [release-process.md](release-process.md).
The package is not published on PyPI: install it from a clone or from a wheel
you built yourself. POSIX only: the packaging is not tested on Windows.

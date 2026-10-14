# Install reference: the old distribution name, and verifying a release

Moved from the README's Install section. The README keeps the install options
themselves and the "Not on PyPI" notice.

## Releases up to 0.3.0 used another distribution name

The command and the module keep their names; only the distribution is called
`tyko84-agent-postbox`. Releases up to 0.3.0 used the distribution name
`agent-postbox`, which pip cannot tell apart from the unrelated PyPI project.
If an environment still has one of those, run
`python3 -m pip uninstall agent-postbox` before installing this version, because
both own `agent_mail.py` and the `agent-postbox` command. (Uninstalling the old
name afterwards instead removes those files; `python3 -m pip install
--force-reinstall ./agent-postbox` puts them back.)

## Verifying a release download

From a clone, one command checks a
release the way a consumer can:

```sh
python3 scripts/verify_release.py v0.5.0
```

It downloads the tag's wheel, sdist and `SHA256SUMS` with `gh release
download`, then checks the hashes, the provenance attestations, the archives'
contents and the builder identity, one `OK`/`FAIL` line per check, exit 1 if
any failed. `gh` must be installed and signed in, for the download and for
`gh attestation verify`. `--no-download --dir DIR` verifies files you already
downloaded; without `gh`, at least check the hashes by hand with
`shasum -a 256 -c SHA256SUMS` (`sha256sum -c SHA256SUMS` on Linux). `--rebuild` also rebuilds the tag with `scripts/repro_build.py`
and compares byte for byte; that needs the hash-pinned build tools
(`python3 -m pip install --require-hashes -r requirements-build.txt`, in a
venv). The same checks as manual commands, with their limits:
[release-process.md](release-process.md), "Verifying the published
release"; the build itself: [packaging.md](packaging.md).

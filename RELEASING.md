# Releasing

## Versioning

[Semantic versioning](https://semver.org/). A breaking change bumps the major, a new feature bumps the minor, and everything else bumps the patch.

What counts as breaking: the `yaybo` command's arguments, the exported file formats, and the table and column names in `store.TABLES`. Adding a column or a subcommand is not breaking; renaming or removing one is. A database written by one version should still be readable by the next, and `yaybo backfill` should be able to bring it forward - a major version is not a licence to break that, only to change what the command line and the exports look like.

The registers change their own markup and their own APIs without warning. Following them is a patch here even when the diff is large, as long as the tables come out the same shape.

## Cutting one

```sh
uv version --bump patch          # or minor, or major
$EDITOR CHANGELOG.md             # move Unreleased into the new version
git commit -am "Release $(uv version --short)"
git tag "v$(uv version --short)"
git push && git push --tags
```

Pushing the tag is the release. `release.yml` checks the tag against `pyproject.toml`, runs the tests, builds, publishes to PyPI and opens a GitHub release with that version's changelog section. A tag that disagrees with the version fails before anything is published.

## First-time setup

Publishing uses [trusted publishing](https://docs.pypi.org/trusted-publishers/), so there is no API token in the repository or in GitHub secrets. It needs two things set up once.

**On PyPI**, at [publishing settings](https://pypi.org/manage/account/publishing/), add a pending publisher:

| field | value |
| --- | --- |
| PyPI project name | `yaybo` |
| Owner | `kiliantscherny` |
| Repository name | `yaybo` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

**On GitHub**, under Settings → Environments, create an environment called `pypi`. Adding yourself as a required reviewer there turns every release into a button you have to press.

## Before tagging

```sh
uv build
tar -tzf dist/yaybo-*.tar.gz
```

The sdist should contain source, the Textual CSS and the test fixtures, and nothing out of `out/` or `exports/`. A published archive cannot be unpublished, and everything this fetches names a real person.

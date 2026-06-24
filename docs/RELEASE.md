# Release process — `fred-macro-mcp`

Releases are cut from `main` after CI is green.

## Steps

1. Ensure `main` is green (the reusable CI workflow + CodeQL).
2. Bump `__version__` in `src/fred_macro_mcp/__init__.py` and the
   `version` in `pyproject.toml`.
3. Move the `## [Unreleased]` section in `CHANGELOG.md` to a dated
   `## [X.Y.Z]` heading and add the comparison link at the bottom.
4. Commit with a conventional message:

   ```bash
   git commit -m "chore(release): vX.Y.Z"
   ```

5. Tag and push:

   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z — <summary>"
   git push origin main
   git push origin vX.Y.Z
   ```

6. Create the GitHub release from the tag, using the CHANGELOG section as the
   notes:

   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z — <summary>" --notes-file <notes> --verify-tag
   ```

## Versioning

Semantic Versioning. A breaking change to a tool's input/output shape, the
env-var contract, or the minimum Python version is a major bump.

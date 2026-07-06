# Releasing research-vault to PyPI

This file covers the one-time setup and the per-release checklist for publishing
`research-vault` to PyPI using **Trusted Publishing** (OIDC — no stored API token).

---

## One-time setup (do this before the first release)

### 1. Create a PyPI account

Go to <https://pypi.org/account/register/> and create an account.
Use an email you control long-term (the author email in `pyproject.toml`).

### 2. Register a Trusted Publisher on PyPI (pending publisher flow)

Because the package has never been published, use the **pending publisher** flow
(you do not need to create the project first — PyPI creates it on first publish).

1. Log in to PyPI → **Your projects** → **Publishing** →
   **Add a new pending publisher**
   (direct link: <https://pypi.org/manage/account/publishing/>)

2. Fill in the form (all values come from the repo URL
   `https://github.com/phoongkhangzhie/research-vault`):

   | Field | Value |
   |---|---|
   | PyPI project name | `research-vault` |
   | Owner | the GitHub username in the repo URL above |
   | Repository name | `research-vault` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. Click **Add**.  PyPI will link this GitHub Actions workflow to your (future)
   package automatically on first publish.

> **GitHub environment note.** The workflow uses `environment: pypi`. Create that
> environment in your repo settings (**Settings → Environments → New environment**)
> before running the workflow. No secrets or protection rules are needed; the
> environment name just has to match what PyPI expects.

### 3. (Optional) Dry-run on TestPyPI first

TestPyPI is a separate staging instance. To test end-to-end before hitting the
real index:

1. Create an account at <https://test.pypi.org/account/register/>.
2. Register a pending publisher at <https://test.pypi.org/manage/account/publishing/>
   with the same fields but set **Workflow name** to `publish.yml` and use
   environment name `testpypi` (or any name you choose — just match the `publish.yml`
   environment field if you create a separate test workflow).
3. Modify `publish.yml` temporarily to point to TestPyPI:
   ```yaml
   - uses: pypa/gh-action-pypi-publish@release/v1
     with:
       repository-url: https://test.pypi.org/legacy/
   ```
4. Push a test tag (e.g. `git tag v0.1.0rc1 && git push origin v0.1.0rc1`).
5. Verify at <https://test.pypi.org/project/research-vault/>.
6. Revert the `repository-url` change before the real release.

---

## Per-release checklist

### Before tagging

- [ ] `pyproject.toml` has the correct `version` (update it now if needed).
- [ ] `uv build && uvx twine check dist/*` passes locally (both PASSED).
- [ ] All 5 CI checks are green on `main` (Test 3.12, Test 3.13, Leakage scan,
      rv lint, rv help --check).
- [ ] `DEVLOG.md` has an entry for this release.

### Tag and push

```bash
git tag v0.1.0
git push origin v0.1.0
```

The `publish.yml` workflow fires automatically on the tag push.
Monitor it at: <https://github.com/phoongkhangzhie/research-vault/actions>

### After publish

- [ ] Verify the package page at <https://pypi.org/project/research-vault/>.
- [ ] Confirm install works in a fresh environment:
  ```bash
  uv venv /tmp/rv-check && uv pip install --python /tmp/rv-check/bin/python research-vault
  /tmp/rv-check/bin/rv --version   # should print: rv 0.1.0
  /tmp/rv-check/bin/rv init /tmp/rv-instance  # should scaffold cleanly
  ```

---

## How the workflow works (no token required)

`publish.yml` uses **PyPI Trusted Publishing** (OpenID Connect):

1. GitHub generates a short-lived OIDC token identifying the workflow run.
2. `pypa/gh-action-pypi-publish` exchanges it for a PyPI upload token.
3. The upload proceeds without any stored secret.

The `permissions: id-token: write` line in `publish.yml` is the only grant needed.
Never add a `PYPI_TOKEN` secret — it is not needed and would be a security risk.

---

## Bumping the version for future releases

1. Edit `version` in `pyproject.toml`.
2. Commit the bump: `git commit -am "chore: bump version to X.Y.Z"`.
3. Follow the **Per-release checklist** above.

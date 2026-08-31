# Alert fixture cleanup

The committed Otodom, Morizon, and Gratka examples are synthetic contract
fixtures. They contain only example addresses, reserved URLs, and fields useful
to a future parser. Never replace them with a mailbox export.

## Required owner actions

The original delivered messages remain in repository history until the owner
coordinates a destructive rewrite. Before rewriting history:

1. Recreate the three portal alerts so old personalized alert, account, and
   unsubscribe links are invalidated. Review the receiving Google account for
   unexpected sessions and revoke any that are not recognized. If a portal does
   not invalidate old links when an alert is recreated, ask its support team to
   revoke them.
2. Notify every collaborator, pause merges and deployments, and arrange fresh
   clones after the rewrite. Forks, local clones, pull-request refs, and caches
   may retain the removed objects and must be handled separately.
3. Temporarily allow the coordinated force-push only after recording the branch
   protection and release-tag settings that must be restored.

From a fresh mirror clone, remove the exact three original paths from every
branch and tag with `git-filter-repo`:

```bash
git clone --mirror git@github.com:deepkuba/homez.git homez-cleanup.git
cd homez-cleanup.git
git filter-repo --force --invert-paths \
  --path 'data/email_examples/GRATKA Odkryj 2 nowe oferty nieruchomości dla Ciebie! - 31 sierpnia 15_01.eml' \
  --path 'data/email_examples/MORIZON Odkryj 4 nowe oferty nieruchomości dla Ciebie! - 31 sierpnia 15_00.eml' \
  --path 'data/email_examples/OTODOM Kraków +10km, mieszkania na sprzedaż, rynek pierwotny i wtórny.eml'
```

Before pushing, confirm that the original blob IDs and paths are absent from all
reachable refs:

```bash
git rev-list --all --objects | grep -E \
  '13afd55258ff944b1bc29d63d87911e87169187a|717890e22d2cc5349816852bf246b6f6c50c816c|be6be700f26065c701722ab7fad61b53397f19dc'
git log --all --name-only --format= -- data/email_examples | sort -u
```

The first command must print nothing. The second must not list any of the three
old filenames. Run the organization's secret/PII scanner across all refs in the
mirror and manually inspect every remaining `.eml` blob. Only then, with the
owner present, force-push the rewritten branches and tags, restore protections,
and require collaborators to delete old clones and clone again:

```bash
git remote add origin git@github.com:deepkuba/homez.git
git push --force --mirror origin
```

GitHub support may need to purge cached views and pull-request refs. Treat the
cleanup as incomplete until the remote scanner and `git rev-list --all` checks
both pass on a fresh post-rewrite clone.

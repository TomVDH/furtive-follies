# Releasing furtive-follies

Two things happen at a release boundary and at no other time.

## 1. Bump the version

```bash
python3 scripts/bump_plugin_version.py adjudant <X.Y.Z>
```

That writes all four lockstep files at once: `adjudant/.claude-plugin/plugin.json`,
`adjudant/scripts/command-metadata.json`, `adjudant/skills/adjudant/SKILL.md`, and
this repo's `.claude-plugin/marketplace.json`. Never edit them by hand; the
`version-consistency` validator fails the commit if they disagree.

## 2. Regenerate the field guide, if it is behind

```bash
python3 scripts/check_field_guide.py
```

Exit 0 means nothing to do. Exit 1 lists what disagrees: a verb card the guide
still shows, a verb it has no card for, or a spelled-out count that is wrong.

`field-guide.html` is 1.4 MB and `field-guide.pdf` is 5.7 MB, nearly all of it
embedded screenshots. **Regenerate both together, at a release boundary only.**
Doing it per change would push megabytes of near-identical binary into history
for a one-word edit, and the screenshots are shot by hand against a staged
vault (`onboarding/SCREENSHOTS.md` is the runbook).

The checker is not a pre-commit hook and not a CI step. It is a reporter you
run when you are already releasing. A gate that goes red on every verb change
trains people to skip the hook that also runs the validators.

## What is NOT part of a release

`adjudant/` is generated from the marketplace repo by
`scripts/generate_twin.py` over there. Do not hand-edit anything under
`adjudant/` here: the next regeneration overwrites it, and the marketplace's
`test_twin_parity` fails until it does. Fix it in the marketplace and
regenerate.

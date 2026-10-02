# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

All five now exist on the remote. They were created on 2026-10-01; before that,
`wontfix` was the only one of the five, and a triage skill that applied
`needs-triage` to an issue would have failed on a label that did not exist.

Recreate one with:

```sh
gh label create needs-triage --color "827717" --description "Maintainer needs to evaluate this issue"
```

Colours: `needs-triage` `827717`, `needs-info` `d876e3`, `ready-for-agent`
`0e8a16`, `ready-for-human` `1d76db`.

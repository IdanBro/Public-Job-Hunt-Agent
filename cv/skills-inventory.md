# Skills Inventory

True skills and facts that aren't in `master.yaml`, each with its evidence and the level it may be claimed at. **The agent may use only `master.yaml` and this file when building or tailoring a CV.** Anything that isn't in one of the two is off-limits.

> The candidate is fictional (see `cv/master.yaml`). Replace this file with your own.

## Rules
- **Never name personal projects** (here: "ProjectNebula") on the CV. Skills learned there may appear in the Skills section and in the unnamed "Independent DevOps & AI Projects" entry, never in an employer's bullets.
- Claim each skill **at its stated level** only. "Working knowledge" may be listed as a skill, never as deep production ownership. "Familiar" goes in the Skills section only.
- Employers, titles, dates and numbers never change.

## Which skills go on which CV
`cv/skill-policy.yaml` is the source of truth. Three tiers:
1. **Always**: on every CV.
2. **Variant**: always on that variant's CV (DevOps or Software). On the other variant's CV only when the job ad mentions it.
3. **Job**: only when the job ad mentions it.

`validate_tailored` rejects a CV missing any "always" skill or its own variant's skills, and any skill outside the CVs and the policy.

## Skills

| Skill | Level | Evidence |
|---|---|---|
| Argo CD | Working knowledge | Northwind Cloud: ran GitOps sync for two internal services |
| Docker Compose | Professional | Local development stacks at both employers |
| Microservices | Professional | Globex Data and Northwind Cloud |
| JUnit | Professional | Globex Data: the integration-testing libraries |
| pytest | Working knowledge | Python tooling at Globex Data, personal projects |
| Linux | Professional | Throughout |
| Mentoring | Professional | Onboarded new platform engineers at Northwind Cloud |

## Not claimed
- **Go, Rust**: never list them, even when an ad asks.

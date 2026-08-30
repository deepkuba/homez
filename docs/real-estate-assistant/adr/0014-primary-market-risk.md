# ADR 0014: Include primary-market projects with evidence-based risk assessment

- Status: Proposed
- Date: 2026-08-30

## Context

The buyer accepts developer and under-construction apartments up to an absolute
three-year horizon, working date 30 August 2029. They require finishing-cost
estimates and want to understand whether construction may fail or be delayed.

No automated system can guarantee that a project will complete. Marketing claims,
developer group reputation, and statutory protection of buyer payments answer
different questions and must not be collapsed into one “trusted” badge.

## Decision

Include primary-market candidates when their promised timeline fits the approved
deadline and their evidence reaches a minimum confidence threshold. Distinguish:

- construction completion;
- occupancy/use permit where required;
- key/handover date;
- finishing duration and habitable date;
- establishment and transfer of separate ownership.

### Project and developer dossier

Collect or request verification of:

1. **Buyer-payment protection:** information prospectus, open/closed housing trust
   account, DFG coverage/applicability, payment milestones, and bank confirmation.
2. **Corporate identity:** exact contracting/project company, KRS/NIP, group and
   parent relationship, representation, capital, filings, and project-SPV risks.
3. **Financial evidence:** timely financial statements, equity, debt, liquidity,
   cash flow, audit qualifications/going-concern notes, and guarantees. Ratios are
   warning signals, not solvency predictions.
4. **Insolvency/enforcement signals:** current restructuring or insolvency records,
   relevant court/official notices, and material disclosed proceedings.
5. **Project legality:** land title and encumbrances, final building permit/status,
   approved project identity, planning context, and required infrastructure.
6. **Execution evidence:** contractor, physical progress versus schedule, milestone
   slippage, construction pauses, financing changes, and independent site evidence.
7. **Track record:** comparable completed projects, promised versus actual delivery,
   defects/acceptance history, and whether the same people/entities were involved.
8. **Consumer/contract signals:** public UOKiK decisions, prospectus consistency,
   price-adjustment terms, delay remedies/penalties, acceptance/defect procedures,
   withdrawal rights, and independent legal review.

Identify whether evidence concerns the contracting SPV, parent group, contractor,
or project; do not transfer a parent brand's reputation to an unsupported SPV.

### Risk output

Report separate dimensions rather than one unexplained score:

- buyer-funds protection;
- legal/permit readiness;
- developer financial/corporate risk;
- construction progress/schedule risk;
- contractual protection;
- evidence completeness and freshness.

Each dimension is `lower concern`, `watch`, `higher concern`, or `unknown`, with
facts and source dates. Summarize overall concern and confidence, but never state
that completion is certain. Known serious legal risk remains ineligible even for
exploration. Insufficient evidence may restrict a candidate to exploration or a
manual-review queue.

### Finishing economics

For shell/developer-standard property, create an itemized low/base/high finishing
estimate plus contingency. Normal eligibility uses:

`purchase + mandatory extras + high finishing estimate + contingency <= comparable ready-to-live price`

Show the assumed standard, area, included/excluded developer scope, quote date,
and confidence. Update after measured plans and contractor quotations.

### Automation boundary

Use official public registers where their access terms and interfaces permit it.
Some official services use CAPTCHA or otherwise protect bulk access. Do not bypass
those controls; create a dated manual verification task and retain the resulting
document/reference where lawful.

## Consequences

- Under-construction bargains can be considered without treating statutory fund
  protection as a completion guarantee.
- Primary-market enrichment is partly manual and document-driven.
- The digest must display missing critical checks prominently.
- Developer/project facts require periodic refresh through handover and ownership
  transfer.

## Current official references

- Developer Act, including prospectus, housing trust accounts, agreements, buyer
  rights, and DFG:
  https://eli.gov.pl/api/acts/DU/2021/1177/text.html
- Government summary of DFG buyer-payment protection:
  https://www.gov.pl/attachment/ba1137e4-fe5a-412c-b1d2-27e8a0cbf11c
- Ministry of Justice KRS and financial-document services:
  https://prs.ms.gov.pl/krs
- GUNB public construction application/permit register:
  https://wyszukiwarka.gunb.gov.pl/

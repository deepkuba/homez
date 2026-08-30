# ADR 0005: Treat renovation as an uncertain value trade-off

- Status: Proposed
- Date: 2026-08-30

## Context

The buyer prefers move-in-ready homes but has stable accommodation and may accept
additional time and expense if a property is significantly cheaper. They also
slightly prefer homes that remain habitable while improvements are staged.

Listing text and photographs cannot establish renovation costs precisely. Hidden
conditions, measurements, desired finish, building constraints, and contractor
availability materially affect cost.

## Decision

Use a staged estimate:

1. **Listing stage:** classify visible work, extract claimed condition, and
   provide low/base/high estimates with confidence and missing facts.
2. **Viewing stage:** complete a structured checklist, collect measurements,
   installation ages, defects, and building restrictions.
3. **Due-diligence stage:** obtain a technical inspection and itemized contractor
   quotations before treating cost as sufficiently reliable for a purchase
   decision.

Calculate effective all-in price using expected work and a separate risk reserve.
Compare it with similar move-in-ready candidates. Show whether the discount
survives the high-cost scenario.

A renovation candidate is eligible for normal results only when:

`purchase price + high renovation estimate + contingency <= comparable move-in-ready price`

The comparison must control for usable area/layout, location or commute,
building scale, floor/elevator, parking, and material amenities. Where taxes,
mandatory ancillary purchases, or fees differ, compare like-for-like effective
costs. Store the comparable set and confidence; insufficient evidence must not be
presented as a precise market value.

Use the conservative high estimate for the eligibility decision. Keep the base
estimate for planning and explanation only. If the high estimate is unavailable
or materially unreliable, the candidate cannot pass this rule automatically.

Maintain a separate **habitability during works** assessment covering safe
electrics, water, heating, bathroom, basic cooking, a clean sleeping room, and
isolation from hazardous/dust-heavy work. Give this a small positive weight.

## Consequences

- Renovation candidates can enter normal results when their adjusted value is
  no worse than comparable move-in-ready value and other constraints pass.
- AI/photo analysis can triage work but cannot be represented as a quote.
- Estimates must include date, locality, standard, VAT assumptions, included and
  excluded scope, and confidence.
- The system needs feedback after quotations/completed work to calibrate future
  estimates.

# Buyer Profile

- Status: Discovery draft
- Updated: 2026-08-30

## Hard constraints

| Dimension | Rule | Clarification needed |
| --- | --- | --- |
| Transaction | Purchase only; no rental arrangements | None |
| Possession | No other tenants; property should be delivered vacant | Confirm whether an owner temporarily remaining after closing is also forbidden |
| Legal title | Separate ownership (`odrębna własność lokalu`) required for normal matches | Cooperative ownership right (`spółdzielcze własnościowe prawo do lokalu`) may appear only as an explicitly labeled exploration result |
| Mortgage/encumbrance | Clean title is required as the transaction outcome | A seller mortgage is acceptable only with a documented lender payoff and mortgage-removal procedure; its presence and unresolved steps must be conspicuous in every report |
| Location | Krakow or a surrounding locality from which ul. Podbrzezie 6, 31-054 Krakow is reachable within 45 minutes door to door | Test typical weekday arrival around 08:30 and return around 17:30; the slower direction determines eligibility; Wieliczka and Niepolomice may qualify; Skawina is explicitly excluded from normal matches; additional destinations with independent routing goals may be added |
| Building scale | Apartment preferred; large multi-unit buildings are strongly disfavoured because the buyer prefers fewer neighbors | Classify primarily by apartments sharing one entrance: up to 20 ideal, 21-40 acceptable, 41-80 strong penalty, more than 80 exploration only |
| Floor | Not the top floor | Confirm whether a setback/penthouse beneath a roof is always excluded |
| Ground floor | Allowed only with a private garden | Define legally assigned garden versus informal/exclusive-use arrangement |
| Elevator | Required above the third floor | Clarify Polish floor convention: elevator required for `4. piętro` and above |
| Parking | Required possibility in remote locations | Define ownership/rental/street-parking acceptability and maximum extra price |
| Transport | Strong public transport required in central locations | Define minimum frequency and maximum walk to a stop |
| Household/layout | Home for one person, with possible future partner; studios excluded | At least two genuinely usable rooms: a living room and a separate office/bedroom |
| Area | Minimum 40 m2 | Preferred band is 48-55 m2; layout quality takes precedence within eligible homes |

## Strong preferences

- Ready to move in.
- Avoid finishing or renovation where possible.
- If work is required, show an explicit estimated cost range and include it in
  the effective all-in price.
- Slight preference for being able to occupy the apartment safely while
  non-critical work remains.
- Quiet setting; avoid noisy roads.
- Balcony.
- Nearby green space.
- Separate kitchen rather than a kitchenette/open kitchen.
- Small residential building with relatively few neighbors.

## Layout interpretation

- Minimum functional program: one living room plus one separate closable room
  suitable for daily office use and sleeping.
- A listing described as two rooms should not pass automatically: the tool should
  check room dimensions, access, daylight, and whether the second room can hold
  the intended furniture.
- A separate kitchen adds score but is not currently an eligibility requirement.
- Suitability for a possible future partner is desirable, but the search should
  not pay a large premium solely for that uncertain scenario.
- Minimum area is 40 m2 and the ideal band is 48-55 m2. Area is not a substitute
  for layout: the living room and separate office/bedroom must still be usable.

## Proposed derived checks

These should be calculated by the tool rather than trusted blindly from listing
descriptions:

- route time to a buyer-defined city-centre destination at relevant hours;
- road and tram proximity plus a conservative noise-risk flag;
- walking distance to usable green space;
- floor/elevator consistency;
- estimated renovation class and low/base/high cost range;
- current habitability and whether essential bathroom, cooking, heating, water,
  electrical, and sleeping functions can be used during staged renovation;
- effective all-in acquisition cost;
- land-and-mortgage-register and possession due-diligence checklist status;
- confidence and missing-data flags for every inferred attribute.
- conspicuous current encumbrance state: verified clean, seller mortgage with
  documented discharge required, other disclosed entry, unknown, or serious risk.
- commute margin in minutes for every displayed property: positive margin under
  the limit or explicit minutes over the limit.
- building-scale estimate based on total dwellings, dwellings per entrance,
  entrances/stairwells, and floors; avoid using construction age as a proxy.

## Delivery preference

- Every regular report should contain the same number of exploration listings as
  preferred/compliant listings—a 1:1 mix.
- Initial weekly target: 10 preferred/compliant listings plus 10 exploration
  listings.
- Portal alerts may be ingested through a dedicated managed mailbox.
- Each listing card should link to a mobile-friendly feedback page using an
  expiring, listing-scoped token.
- Each listing should also be easy to forward without exposing private feedback
  tokens: provide a prefilled share-safe email and a token-free copy block.
- Weekly report delivery: Friday at 10:00 Europe/Warsaw time.
- If fewer than 10 compliant listings are available, include all worthwhile
  compliant listings and still allow up to 10 exploration listings; do not pad
  either section with stale or low-information duplicates.
- The exploration listing must name every failed or uncertain filter and explain
  what exceptional quality justified showing it.
- It must never be presented as eligible or mixed into the ranked compliant
  results.
- The exploration listing may violate ordinary property, location, commute, and
  price rules, including the normal exclusion of Skawina.
- It may never be a rental, lack vacant possession, or have a known serious
  legal-title risk.
- A cooperative ownership right fails the normal separate-ownership rule but is
  allowed in the exploration section when no separate serious legal risk is
  known.

## Still needed to make matching useful

- Office/bedroom furniture requirements for evaluating whether the second room
  is genuinely usable.
- Confirm whether any unused credit-card or overdraft limits exist; otherwise no
  material financial obligations are reported.
- Define the minimum post-closing emergency reserve and maximum acceptable
  recurring non-loan housing costs.
- Whether houses, terraced homes, or converted buildings should ever appear.
- Expected purchase date and intended ownership horizon.
- Maximum acceptable renovation cash requirement and duration.

## Renovation tolerance

- The buyer has stable current accommodation and can tolerate time before moving.
- A property requiring work may qualify when its price reduction creates a
  significant advantage after realistic work and risk costs.
- Hard value rule: the renovation candidate's purchase price plus the
  conservative high renovation estimate, including contingency, must not exceed
  the price of a sufficiently similar move-in-ready property. Otherwise it is
  ineligible for normal results.
- The buyer requires renovation scope and cost uncertainty to be explicit.
- Occupancy during works is a slight preference rather than a hard requirement.
- Precise cost cannot be established from an advertisement alone; it requires a
  technical inspection, measurements, defined standard, and contractor quotes.
- Similarity must consider at least usable area/layout, location or commute,
  building scale, floor/elevator, parking, and material amenities; weak
  comparables lower confidence rather than creating false precision.

## Confirmed soft-rule behavior

- Road-noise risk is a strong scoring penalty, not a hard exclusion. Evidence
  such as courtyard-facing windows, setbacks, barriers, floor, measurements, or
  viewing feedback may distinguish a quiet apartment from its road proximity.
- Both direct-owner and agency listings are eligible. No blanket agency penalty
  applies, but every known or estimated buyer-side commission must be visible and
  included in effective all-in price.

## Primary-market scope

- Developer and under-construction apartments are eligible.
- Working absolute deadline is three years from profile date: 30 August 2029.
  Whether this deadline applies to handover, separate-ownership transfer, or both
  requires confirmation.
- Show a conservative high finishing-cost estimate plus contingency and include
  it in effective all-in price and the renovation-value rule.
- Provide a developer and project risk dossier with evidence, missing checks,
  confidence, and warning signs. Never claim completion is guaranteed.

## Provisional financing profile

- First residential-property purchase.
- Mortgage financing is most likely.
- PLN 200,000 is the complete acquisition-cash budget. It must cover the down
  payment, legal/notarial and registration fees, required bank products or
  insurance, and other closing requirements.
- Approximate monthly net income: PLN 10,000 from stable employment under an
  employment contract (`umowa o prace`).
- Tentatively comfortable mortgage instalment: PLN 4,000/month, explicitly
  excluding administration, utilities, insurance, parking, and maintenance;
  subject to stress testing.
- An additional post-closing emergency reserve will be maintained separately;
  its amount has not yet been specified and is not available for acquisition.
- No other loans or fixed financial obligations are reported, apart from up to
  approximately PLN 1,000 of short-lived 0% online-purchase instalments that can
  be closed at any time.

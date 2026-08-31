# Domain Glossary

| Term | Working definition |
| --- | --- |
| Buyer profile | Versioned collection of hard constraints, weighted preferences, locations of interest, and delivery settings for one buyer. |
| Hard constraint | A condition whose failure makes an offer ineligible, such as maximum total price. Hard constraints must not be weakened by learned preferences. |
| Preference | A desirable but negotiable property of a home. It contributes to ranking and can have a weight or utility curve. |
| Listing | One advertisement published by one source. It can change or disappear over time. |
| Property candidate | The real-world home inferred from one or more possibly duplicate listings. |
| Listing snapshot | Time-stamped observation of a listing's price, description, availability, and other mutable fields. |
| Source adapter | Isolated integration that retrieves offers from one permitted source and maps them to the common listing model. |
| Normalization | Conversion of source-specific fields and units into a common schema. |
| Deduplication | Linking advertisements believed to describe the same real-world property. |
| Eligibility | Whether a property candidate satisfies every active hard constraint. |
| Tri-state rule | A hard-rule result of `pass`, `fail`, or `unknown`; unknown evidence is never silently treated as a pass. |
| Match score | Explainable measure of how well an eligible property satisfies weighted preferences. It is not an investment valuation. |
| Effective all-in cost | Acquisition price plus mandatory extras, renovation/finishing, and contingency; the conservative high value is used for renovation eligibility. |
| Confidence | Estimate of how reliable an extracted or inferred fact is. Unknown facts should remain unknown rather than be treated as negative. |
| Feedback event | Buyer action or stated reason that may tune future ranking, such as saving a home or rejecting it due to noise. |
| Digest candidate | A property eligible for the next digest because it is new, materially changed, newly high-ranking, or exceptional value. |
| Weekly digest | Email containing a small ranked set of digest candidates plus concise explanations and source links. |
| Compliant/exploration slate | Two separate ranked result sets: compliant homes satisfy every hard rule, while exploration homes visibly explain exceptions and remain subject to safety exclusions. |
| Source policy | Recorded access method, terms, robots behavior, rate limit, and retention constraints for a listing source. |
| Vacant possession | Contractual and verified delivery of the home without tenants or other occupants whose rights or presence would prevent immediate possession. |
| Effective all-in price | Purchase price plus mandatory ancillary property, transaction costs, and estimated immediate works needed for the buyer's intended move-in standard. |
| Legal-title status | Structured summary of the ownership form, land-and-mortgage register, disclosed rights/claims, and unresolved due-diligence items; never inferred solely from marketing text. |
| Travel-time rule | Route requirement defined by destination, mode, departure window, maximum duration, and acceptable reliability. |
| Search area | Dynamic set of candidate locations that pass the travel-time rule plus explicit geographic inclusions/exclusions; not simply an administrative boundary. |
| Exploration listing | A deliberately selected, clearly labeled property outside the normal eligible set, included to reveal whether a filter is overly restrictive; reports target one exploration result per compliant result. |
| Separate ownership | `Odrębna własność lokalu`: the required legal form for normal matches, subject to register and document verification. |
| Cooperative ownership right | `Spółdzielcze własnościowe prawo do lokalu`: a transferable limited property right, not separate ownership; eligible only for this buyer's exploration section. |
| Candidate score | Explainable pre-selection score calculated after hard-filter evaluation; not itself the final email order. |
| Weekly slate | Diversified set of listings selected for one digest after scoring, deduplication, freshness checks, and repetition controls. |
| Material change | Change worth resurfacing a known candidate, such as meaningful price reduction, corrected legal/condition facts, or newly favorable commute/value data. |
| Near miss | Ineligible property that narrowly fails one or a small number of filters while scoring strongly on the remaining needs. |
| Commute destination | A versioned geocoded address against which door-to-door travel is measured; the initial destination is ul. Podbrzezie 6, Krakow. |
| Routing goal | Versioned per-destination rule containing maximum journey time, direction/time windows, allowed modes, whether it is required or preferred, and scoring behavior. |
| Route observation | A provider result for one mode and direction, retained with its query time, duration, confidence, provider, and routing-goal version. |
| Route freshness | Whether a route observation is within the routing goal's configured age; stale or missing observations are unknown for hard eligibility. |
| Building scale | Estimated resident density described by dwelling count, dwelling count per entrance/stairwell, number of entrances, and floors; distinct from construction technology or age. |
| Entrance-level dwelling count | Estimated number of apartments whose occupants normally share the candidate apartment's exterior entrance and circulation core; the primary building-scale measure for this buyer. |
| Seller mortgage | Mortgage currently securing the seller's debt; acceptable to this buyer only when a verified transaction procedure repays the lender and enables deletion of the mortgage from the register. |
| Clean-title outcome | Required transaction result in which ownership transfers without surviving seller mortgage or other unacceptable right/claim; distinct from the register being clean when the listing is first discovered. |

## Terms requiring clarification

- **Krakow**: administrative city boundary, commute radius, or named districts.
- **Total price**: asking price alone or asking price plus parking, storage,
  renovation, taxes, notary fees, and estimated transaction costs.
- **Best match**: highest preference score, best value, or a diversified blend.
- **Automatic browsing**: official API/feed, portal alerts, email ingestion, or
  browser-based collection.

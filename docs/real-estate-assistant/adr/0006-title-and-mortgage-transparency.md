# ADR 0006: Permit documented seller-mortgage discharge and show it clearly

- Status: Proposed
- Date: 2026-08-30

## Context

The buyer wants ownership without an inherited mortgage or serious legal-title
risk. Excluding every property with a seller mortgage before sale would remove
otherwise ordinary candidates. The buyer accepts such properties when the
transaction reliably produces clean title, but wants the situation made clear.

## Decision

A seller mortgage does not itself make a listing ineligible. Classify and display
encumbrance state prominently as one of:

1. **Verified clean at check time**;
2. **Seller mortgage—documented discharge required**;
3. **Other disclosed register entry—legal review required**;
4. **Unknown—not yet verified**;
5. **Known serious title risk—ineligible**, including for the exploration slot.

For state 2, normal eligibility requires evidence appropriate to the transaction
stage, eventually including current register review, lender payoff information,
the lender's required conditions/documents, payment routing, and the instrument
needed to remove the mortgage. A qualified lawyer/notary and financing bank must
verify the actual procedure and documents.

The report must never translate missing listing information into “no mortgage.”
It should show the last verification time, source, unresolved steps, and whether
the assessment is based only on seller/agent claims.

For a primary-market property where separate ownership does not yet exist, show
the current legal stage, land register, contractual path and deadline to establish
and transfer separate ownership. Do not label the future title as currently
verified ownership.

## Consequences

- Legitimate mortgaged listings remain discoverable.
- The buyer can distinguish an ordinary documented discharge from unclear or
  unacceptable encumbrances.
- Legal verification is a staged workflow, not something the crawler can finish
  from an advertisement.
- Register data and documents require careful handling and access controls.

## Official legal references

- Consolidated Land and Mortgage Registers and Mortgage Act:
  https://eli.gov.pl/api/acts/DU/2023/1984/text.html
- Consolidated Housing Cooperatives Act:
  https://eli.gov.pl/api/acts/DU/2024/558/text.html

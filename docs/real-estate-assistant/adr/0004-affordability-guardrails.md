# ADR 0004: Separate affordability from bank eligibility

- Status: Proposed
- Date: 2026-08-30

## Context

The buyer expects mortgage financing, has up to PLN 200,000 including obligatory
fees, earns approximately PLN 10,000/month, and tentatively considers a PLN 4,000
monthly payment comfortable.

A bank's maximum approved loan is not the same as a sustainable personal budget.
Interest rates can reset, ownership creates recurring costs, and using all cash
at closing would remove the emergency reserve.

## Decision

The assistant will maintain separate values for:

1. bank-indicated borrowing capacity;
2. buyer-approved comfortable instalment;
3. stressed instalment under higher-rate scenarios;
4. recurring non-loan housing costs;
5. cash required at closing;
6. protected post-closing reserve;
7. immediate move-in/renovation budget.

It will classify listings into core, stretch-review, exploration, or unaffordable
bands. It will not infer a safe debt level from income alone or present a lender's
maximum as a recommendation.

## Consequences

- The digest can explain why a property falls into a stretch band.
- Mortgage assumptions must be versioned and refreshed.
- Final affordability requires personalized offers and the buyer's complete
  financial picture.
- Changes in rates must not silently rewrite the buyer's comfort preference.

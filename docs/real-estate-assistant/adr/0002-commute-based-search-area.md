# ADR 0002: Define the search area by commute, not city boundary

- Status: Accepted
- Date: 2026-08-30

## Context

The buyer accepts homes inside Krakow and in nearby localities such as Wieliczka
or Niepolomice, but explicitly rejects Skawina. The meaningful constraint is a
maximum 45-minute door-to-door journey to ul. Podbrzezie 6, 31-054 Krakow. More
commute destinations, each with its own routing goal, may be added later.

A district or municipality allowlist is a poor proxy: two homes in the same town
can have very different access to rail, buses, trams, parking, and the final
walking segment.

## Decision

Determine geographic eligibility in two stages:

1. Reject explicit geographic exclusions such as Skawina.
2. For all other candidates, calculate door-to-door routes for each active routing
   goal under all supported travel modes and that goal's time windows. Walking,
   cycling, driving, trains, buses, and trams may qualify. For the initial required
   goal, require the fastest realistic journey to ul. Podbrzezie 6 to take no more
   than 45 minutes.

Store the route provider, query time, winning mode, alternative modes,
departure/arrival assumptions, duration, transfers, walking/waiting time, parking
assumptions, and confidence. Refresh travel results because schedules and road
conditions change. Driving is door to door only after realistic parking/search
and final walking time are included.

Evaluate a typical weekday arrival around 08:30 and a return around 17:30. The
slower of the two fastest realistic door-to-door journeys determines whether the
property passes. This avoids accepting a location based only on its easier
direction.

For every property included in a digest, display both journey times and the
eligibility margin. Examples: `passes by 7 min` for a 38-minute controlling
journey, or `fails by 12 min` for a 57-minute controlling journey. A failed
commute normally makes the property ineligible, but it may still appear in the
exploration slot with the miss quantified.

Represent destinations and routing goals as data rather than code. Each goal
stores a label, normalized address, provider place identifier/coordinates,
maximum time, arrival and return windows, allowed modes, hard-versus-soft role,
validity dates, and independent scoring/display settings. Re-geocoding or editing
an address creates a new version and invalidates affected cached routes.

When multiple goals exist, every hard/required routing goal must pass for normal
eligibility. Soft/preferred goals affect scoring without rejecting the property.
Exploration results state each failed goal and its excess journey time.

Where a listing lacks a precise address, calculate a conservative range from the
available map point or street and mark the result uncertain. Do not claim that a
property passes a hard commute rule from a district centroid alone.

## Consequences

- Wieliczka and Niepolomice are candidates, not automatic inclusions.
- Homes just outside Krakow can outrank poorly connected homes inside Krakow.
- Routing becomes an external dependency and a recurring operating cost.
- Public-transport results require a timetable-aware provider; typical traffic
  is needed for car journeys.
- Listings with hidden or imprecise addresses may require manual commute review.

## Open details

- Whether mixed journeys such as car plus park-and-ride count.
- How to estimate parking-search time consistently for driving.
- Whether a later version should use a percentile/reliability target in addition
  to typical weekday duration.

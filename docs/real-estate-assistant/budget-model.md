# Budget Model

- Status: Discovery draft
- Updated: 2026-08-30

## Why the tool needs an all-in budget

The listing price is not the amount needed to buy and occupy a home. For each
candidate the tool should calculate three figures:

1. **Acquisition price**: negotiated purchase price plus mandatory parking or
   storage.
2. **Cash needed at closing**: equity/cash purchase amount plus applicable tax,
   notary/court costs, financing costs, and intermediary fees.
3. **Move-in cost**: acquisition price plus immediate finishing, renovation,
   furnishing, and a contingency reserve.

## Current Krakow planning anchor

Current market evidence should be refreshed automatically and labeled by data
date. As of the latest sources reviewed on 2026-08-30:

- recent notarial-data analysis reports a Krakow secondary-market median around
  PLN 14,690/m2, with the middle half roughly PLN 12,722-17,003/m2;
- July 2026 active listings average around PLN 16,828/m2, illustrating that
  asking prices and completed transaction prices are different measures.

Until area and layout are known, a useful first-pass purchase-price envelope is:

| Area | At PLN 12,700/m2 | At PLN 14,700/m2 | At PLN 17,000/m2 |
| ---: | ---: | ---: | ---: |
| 40 m2 | PLN 508k | PLN 588k | PLN 680k |
| 50 m2 | PLN 635k | PLN 735k | PLN 850k |
| 60 m2 | PLN 762k | PLN 882k | PLN 1.02m |
| 70 m2 | PLN 889k | PLN 1.029m | PLN 1.19m |

These are broad market anchors, not valuations. The buyer's requirements—quiet,
ready-to-live, balcony/green space, suitable elevator, and useful transport or
parking—may select above-median properties even outside the centre.

The known minimum program is a living room plus a separate office/bedroom, with a
separate kitchen preferred. The hard minimum is 40 m2 and the ideal band is
48-55 m2. At the broad market anchors above, the ideal band corresponds to
approximately PLN 610k-935k before transaction and move-in costs. The financing
profile narrows normal results to the approved core/stretch price bands rather
than treating the top of this market range as affordable.

## Cost fields to model

- PCC on eligible secondary-market purchases; whether the first-home exemption
  applies must be confirmed.
- Notarial deed, certified copies, and land-register court fees.
- Agent fee, if charged to the buyer, including VAT.
- Bank valuation, loan origination, insurance, and mortgage-registration costs
  when financed.
- Parking/storage purchase price and recurring fees.
- Renovation/finishing low, base, and high estimates plus contingency.
- Immediate furniture/appliances.
- Technical inspection and legal review.

## Budget discovery inputs

- Required floor area and number of rooms.
- Whether this is the buyer's first residential property.
- Cash available without consuming the emergency reserve.
- Mortgage eligibility and comfortable—not merely bank-maximum—monthly payment.
- Desired reserve after closing.
- Maximum acceptable immediate works and their funding source.

## Provisional affordability scenarios

Known inputs are PLN 200,000 total acquisition cash, approximately PLN 10,000 net
monthly income from stable employment under an employment contract, and a
tentative PLN 4,000 comfortable mortgage instalment. The instalment limit does
not include recurring ownership costs. The acquisition cash must cover equity,
legal/notarial and registration costs, and any bank-required insurance/products.
A separate emergency reserve will exist but must not be used to make a listing
appear affordable.

For an equal-payment mortgage, PLN 4,000/month implies approximately:

| Illustrative annual rate | 25-year principal | 30-year principal |
| ---: | ---: | ---: |
| 5.5% | PLN 651k | PLN 704k |
| 6.5% | PLN 592k | PLN 633k |
| 7.5% | PLN 541k | PLN 572k |
| 8.5% | PLN 497k | PLN 520k |

These are mathematical scenarios, not loan offers or predictions. They omit
bank-specific commissions, insurance, rate-reset structure, borrower assessment,
and other costs. As of March 2026 the NBP reference rate was 3.75%; a retail
mortgage rate includes additional components and must be taken from a current
personalized offer.

Because the PLN 200,000 also has to fund fees and professional checks, while the
emergency reserve remains outside the acquisition budget, a preliminary search
should use bands rather than a single cap:

- **Core band:** approximately PLN 650k-750k purchase price.
- **Stretch-review band:** approximately PLN 750k-800k, requiring favorable
  financing/cost assumptions and explicit cash-flow review.
- **Exploration only:** above the approved stretch cap until financing inputs are
  validated.

No listing should be described as affordable solely because its asking price is
below loan principal plus PLN 200,000.

Direct-owner and agency listings compete on the same effective-cost basis. For an
agency offer, show the commission basis, percentage or amount, VAT treatment,
whether it is negotiable, and evidence source. If buyer-side commission is not
disclosed, estimate a conservative range, label it unverified, and do not use the
optimistic end to make the listing pass affordability.

For a property requiring work, calculate a **renovation-adjusted advantage**:

`comparable move-in-ready price - (purchase price + high works estimate + contingency)`

Normal eligibility requires this value to be zero or positive: buying plus
renovation must not cost more than a sufficiently similar move-in-ready home. Use
like-for-like effective prices when transaction costs, required parking, or other
mandatory components differ. Do not call a renovation property a bargain merely
because its asking price is lower. Show the low/base/high outcome, comparable set,
and major unknowns. Stable current accommodation reduces schedule pressure but
does not remove financing, hidden defect, or contractor-availability risk.

Apply the same rule to developer-shell property using conservative high finishing
cost plus contingency. Include mandatory parking/storage, developer options,
bank/legal costs, and any period of overlapping housing expense. Keep handover,
occupancy readiness, and formal separate-ownership transfer dates distinct.

No material existing obligations are reported. Approximately PLN 1,000 of
short-term 0% purchase instalments can be closed; the buyer should obtain a
current lender or broker view on how any active products and unused credit limits
affect assessment before relying on calculated borrowing capacity.

## Monthly ownership burden

The digest should display both the loan assumption and a separate recurring-cost
estimate:

- administration/community or cooperative fee;
- heating and other utilities not included in that fee;
- property insurance;
- parking charge;
- property tax where relevant;
- maintenance/replacement reserve.

At a PLN 4,000 mortgage instalment, even a provisional PLN 1,000-1,500 non-loan
envelope produces a PLN 5,000-5,500 total monthly housing burden, or 50-55% of
current net income. This is not automatically unacceptable, but on a single
income it makes an emergency reserve and stress scenario material. Actual
listing-specific fees must replace the provisional envelope.

## Current official references

- NBP decision setting the reference rate to 3.75% from March 2026:
  https://dzu.nbp.pl/GetActPdf.ashx?book=0&position=4&year=2026
- KNF consumer guidance on fixed, periodically fixed, and variable mortgage-rate
  risk, updated 18 August 2026:
  https://www.knf.gov.pl/dla_konsumenta/kampanie_spoleczne/ryzyko_stopy_procentowej?articleId=73671&p_id=18
- Ministry guidance on the first-home secondary-market PCC exemption:
  https://www.gov.pl/web/rozwoj-technologia/kupno-mieszkania-lub-domu-na-rynku-wtornym-bez-podatku-pcc

## Principle

The automation may estimate affordability scenarios, but it should not decide
how much debt is safe. Final financing and legal conclusions require current
offers/documents and appropriately qualified professional review.

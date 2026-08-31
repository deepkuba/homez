# Human To-Do List

- Owner: Buyer
- Updated: 2026-08-31
- Scope: Actions that require a human; coding-agent work is intentionally omitted

Do not put passwords, OAuth tokens, API keys, recovery codes, financial documents,
or personal email content in Git, issues, or this checklist.

## Do now — unblock live listing ingestion

- [ ] Create a dedicated Gmail account used only by the property assistant.
- [ ] Enable 2-Step Verification on that account.
- [ ] Add current recovery email/phone details and store recovery codes securely.
- [ ] Create or update buyer accounts on the initial portals:
  - [ ] Otodom
  - [ ] OLX
  - [ ] Morizon
  - [ ] Gratka
- [ ] Use the dedicated Gmail address for portal notifications.
- [ ] Create broad saved searches/alerts on those portals. Keep them broad enough
  to supply both compliant and exploration results; do not encode every hard rule
  in the portal search itself.
- [ ] Wait until at least one real alert arrives from each portal.
- [ ] Provide one representative alert from each portal for parser tests. Either:
  - remove personal addresses, tracking tokens, and account links yourself; or
  - authorize the coding agent to sanitize a local copy before it becomes a test
    fixture.
- [ ] Review the proposed access method for each portal and explicitly approve it
  only after its current terms and restrictions have been documented.

## Product decisions still needed

- [ ] Resolve the primary-market deadline. Decide what must be complete by
  30 August 2029:
  - keys/handover;
  - finishing and ability to live there;
  - formal transfer of separate ownership;
  - or all three.
- [ ] State whether mixed park-and-ride routes should count when direct public
  transport or driving exceeds 45 minutes.
- [ ] Decide whether houses, terraced homes, or converted buildings may appear in
  exploration results.
- [ ] Define the maximum acceptable recurring non-mortgage housing cost, including
  administration, utilities, insurance, parking, and maintenance.
- [ ] Confirm whether you have unused credit-card or overdraft limits that a bank
  may include in affordability assessment.
- [ ] Define the desired office/bedroom furniture requirements so the tool can
  judge whether the second room is genuinely usable.
- [ ] Define a rough maximum renovation budget and desired finish standard.
- [ ] Approve the final machine-readable buyer profile and review examples near
  every hard threshold before live ranking is enabled.
- [ ] Review the initial scoring weights after seeing the first shadow report.

## When Gmail integration is ready

- [ ] Complete the one-time Google OAuth consent flow using the dedicated Gmail
  account.
- [ ] Store the resulting OAuth token using the encrypted token mechanism; keep
  the encryption key in the VPS secret store and separate from the token file.
- [ ] Grant only the permissions shown in the reviewed setup instructions.
- [ ] Confirm that alert ingestion labels messages correctly and does not modify
  unrelated mail.
- [ ] Confirm the personal email address that should receive reports and failure
  notifications.
- [ ] Never paste OAuth credentials or refresh tokens into chat, source code, or
  documentation.

## When routing integration is ready

- [ ] Create a dedicated Google Cloud project used only by this assistant.
- [ ] Attach a billing account.
- [ ] Enable only the required Google Maps/Routes services.
- [ ] Configure API restrictions and the strongest available VPS/application
  restriction using the deployment instructions.
- [ ] Set Google-side quota limits below the current free allowance.
- [ ] Configure quota and billing alerts.
- [ ] Review the current official free allowance and approve the application's
  lower safety ceiling before entering credentials.
- [ ] Enter the API credential through the provided secret-management mechanism;
  never commit it to Git.
- [ ] Confirm that the quota-exhaustion test email reaches you.

## Before email feedback goes live

- [ ] Choose or approve the HTTPS domain/subdomain for the feedback page.
- [ ] Authorize the required DNS change.
- [ ] Open the email preview on your normal phone and desktop email clients.
- [ ] Verify that compliant and exploration sections are unmistakably different.
- [ ] Test one feedback action from a phone.
- [ ] Confirm that opening a feedback link without submitting does not record an
  action.
- [ ] Test “Share safely” and confirm the prepared message contains no private
  feedback link, token, personal note, or buyer-profile information.
- [ ] Approve at least two shadow reports before enabling Friday 10:00 delivery.

## Before production deployment

- [ ] Authorize the agreed VPS firewall, HTTPS, container, and DNS changes.
- [ ] Enter production secrets through the provided secure mechanism.
- [ ] Create a restricted NAS account/destination for encrypted backups.
- [ ] Keep the backup decryption key somewhere separate from both the VPS and NAS
  backup files.
- [ ] Receive and recognize a test operational-failure notification.
- [ ] Witness or review evidence of a successful database restore from NAS backup.
- [ ] Explicitly approve live Friday 10:00 Europe/Warsaw delivery.

## During the four-week pilot

- [ ] Review every weekly report and use the feedback actions consistently.
- [ ] Flag duplicate properties that were not merged.
- [ ] Flag listings incorrectly marked eligible or ineligible.
- [ ] Report incorrect commute, noise, building-size, legal-form, or cost facts.
- [ ] Review proposed soft-weight changes; approve or reject them explicitly.
- [ ] Do not approve automatic changes to hard rules.
- [ ] Review route quota usage and source/parser failures weekly.
- [ ] Decide after four weeks whether 10 compliant plus 10 exploration listings
  is the right report size.

## When a listing becomes a serious candidate

- [ ] Contact the seller or agent yourself; the assistant must not do this
  automatically.
- [ ] Arrange an in-person viewing and record actual noise, surroundings, entrance
  size, condition, and address accuracy.
- [ ] Obtain an independent technical inspection before relying on renovation or
  condition estimates.
- [ ] Obtain itemized contractor quotations when work is material.
- [ ] Ask a qualified lawyer/notary and the financing bank to verify ownership,
  land-register entries, seller-mortgage payoff/removal, vacant possession, and
  contract terms.
- [ ] Obtain personalized mortgage offers and compare rate-reset risk, total cost,
  required insurance/products, early-repayment terms, and cash needed at closing.
- [ ] Close the small 0% purchase instalments before mortgage assessment if the
  lender or broker advises doing so.
- [ ] Keep the emergency reserve separate from the PLN 200,000 acquisition budget.

### Additional checks for developer property

- [ ] Obtain the current information prospectus and all attachments.
- [ ] Obtain the reservation/developer agreement before signing and have it
  independently reviewed.
- [ ] Verify the exact contracting company/SPV, not just the parent brand.
- [ ] Manually retrieve official records when CAPTCHA or access controls prevent
  lawful automation; do not ask anyone to bypass them.
- [ ] Verify the housing trust account and DFG applicability with documents.
- [ ] Verify land title, encumbrances, permit status, construction milestones,
  promised handover, occupancy readiness, and separate-ownership transfer dates.
- [ ] Review delay remedies, penalties, price-adjustment clauses, withdrawal
  rights, and defect/acceptance procedure with a qualified professional.
- [ ] Treat the tool's risk assessment as a checklist and warning system, not a
  guarantee that construction will finish.

## Ongoing account and security maintenance

- [ ] Keep Gmail and Google Cloud recovery information current.
- [ ] Review account activity and revoke unexpected sessions or credentials.
- [ ] Rotate credentials when exposure is suspected or access changes.
- [ ] Reapprove any portal access method when its terms or implementation changes.
- [ ] Periodically test that reports, quota warnings, failure alerts, and backups
  still work.

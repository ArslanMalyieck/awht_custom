# AWHT Custom

Consolidation of four local ERPNext app working trees: `ar_pdf_export`,
`awht_customizations`, `branch_credit_control`, and `cost_visibility`.
The live production origin is `https://awht.metadaftr.com/`.

**Publisher:** Arslan  
**Contact:** `malikarsaln000009@gmail.com`

**Status: installed and verified on the local v15 site (`erpnext15.localhost`) against a restored live backup. Do not install on the live production server until the exact-version clone/cutover approval is complete.** The four source apps are inactive on the local site after the `awht_custom` hand-off. Live production still runs its original apps and must be backed up and rehearsed before any uninstall.

## Feature Inventory

| Source feature | Current safe implementation | Release caveat |
| --- | --- | --- |
| Cost Visibility Settings, restricted users and patterns | Same DocType names/fields; new owner `AWHT Custom`; redacted Desk reports, exports and standard print bodies | Presentation policy, **not** a general permission boundary; direct API/prints outside named hooks still require normal Frappe permissions |
| Prepared report downloads | Restricted JSON filtered; raw File/private and ZIP routes denied to restricted users | Existing non-Prepared-Report attachments/custom routes are not retroactively sanitized |
| Branded PDF for Query/Script/Custom Reports | Authorized server rerun through the Cost Visibility filter; escaped HTML, File-checked logos, access logging | Prepared reports and exports above 2,000 rows/64 columns not supported; old GET endpoint is not preserved |
| Branded ReportView PDF | Re-fetches and validates user's columns/filters/order/visible rows on the server | Virtual doctypes, complex expressions and grouped row selection not yet supported |
| English/Arabic amount-in-words | Visible selected SAR amount, last nonzero or sum excluding appended total row | Other/ambiguous currencies and some report-specific summary rows require explicit treatment |
| Previous transfer prices | Role-gated previous Material Transfer row history; current/other users' costs cannot be exposed | Source v15 patched-core `old_price` and `get_last_transfer_rate` still need live inventory; only same-UOM history is compared |
| Customer QR and print | Permission-checked linked Customer/Contact/Address info, printable page and PDF | Unlinked cached fields and unreadable Contacts/Addresses are excluded deliberately |
| Sales Invoice per-item tax display and preview | Native ERPNext v16 Item Wise Tax Detail, in-memory draft preview | Unsupported multi-currency/withholding/ambiguous breakdowns remain blank, not guessed; legacy stored values need review |
| Branch invoice warehouse mapping | Explicit, company-scoped Branch/Warehouse mapping; only opt-in invoices, no fuzzy match | Live Branch field names and return behavior must be checked; mappings must be loaded before activation |
| Stock Entry branch credit check | Optional per-company policy with row-summed transfer gate and shared lock | Disabled until configured; **do not cut over** if old live gate is active until policy/mappings/limits match live behavior |
| Material Request credit preview/submit | Optional permission-checked preview and submit validation using provisional stock value | Preview is **not** a reservation and cannot promise future headroom |
| Stock Reconciliation assistant | Duplicate item merge, persistent memory for changed/unchanged rows, remaining-items fetch by warehouse/date range, and qty-preserving posting-date changes | Memory is generated only by the app; manual create/edit/delete is blocked. Draft/delete/cancel lifecycle must be respected |
| Branch Financial Exposure | Original report ported under its original name and `AWHT Custom` module; company/branch scoped with original sales, payments and gross-profit metrics | Amounts use company-currency base fields; the interim `AWHT Current Credit Exposure` remains available |
| Unsold Items | Original report ported under its original name and `AWHT Custom` module; company/warehouse scoped with all original movement sources and filters | The interim `AWHT No Stock Out Items` remains available |
| Sales Invoice salesman-price floor | Original app's check is commented out; no new enforcement | Live ERPNext v15 core patch and actual price-floor policy must be obtained before deciding |

New app endpoints use `awht_custom.*` names. Old `ar_pdf_export.*`,
`awht_customizations.*` and `cost_visibility.*` dotted URLs/queued jobs are not
silently aliased. Inventory actual callers and drain old queued jobs before a
cutover. Do not use the old PDF export code as a workaround: it can bypass
Report role checks and the Cost Visibility filter.

## Site Setup

`awht_custom/install.py` adds only missing fields/roles and fails on an
incompatible existing field. It never overwrites a site's existing field or
imports Cost Visibility settings as fixtures. It creates:

- `Stock Entry Detail.salesman_price` when absent.
- `Sales Invoice Item.item_tax_amount` and `amount_after_tax` when absent.
- `Sales Invoice.custom_branch` and `Branch.custom_credit_limit` when absent.
- `Previous Transfer Prices Viewer` role when absent (no automatic users).
- `AWHT Branch Warehouse` and `AWHT Credit Policy` DocTypes. Credit policy
  defaults **disabled**, deliberately preventing unreviewed live enforcement.

**Important:** These default fieldnames are proven only for this local v16
site. Real production may use `branch`, `custom_branch`, another custom name,
or a patched standard DocField. Installing before comparing live metadata
could create a duplicate field and read the wrong source of truth. Do not
populate credit mappings or enable a policy from warehouse-name guesses.

## Local Verification

Canonical suite on the restored-live v15 site:

```bash
bench --site erpnext15.localhost run-tests --app awht_custom
```

Source-tree/pure-test command (uses the separate v16 bench environment):

```bash
PYTHONDONTWRITEBYTECODE=1 /home/arslan/frappe-bench/env/bin/python \
    -m unittest discover -s awht_custom/tests -q
```

The two commands use different benches. Use the v15 site command for the
current restored-live test site; the standalone path above is kept for the
original v16 source-tree environment.

Latest result (2026-11-24, v15 site): **296 tests, OK, two site-dependent skips**. Client
JavaScript harnesses and syntax checks pass. The merged Stock Reconciliation
assistant was exercised on `Testing - AWH`: a saved draft with one changed and
one unchanged row produced two memory rows, and the Memory rows were excluded
from the next remaining-items fetch.

An isolated sibling-bench test restored a fresh read-only snapshot of local
`localhost` into private socket-only MariaDB and Redis, without web, workers,
scheduler, or outbound network. This is **not** a clone of the production URL.

- `awht_custom` installed successfully on the clone.
- Cost Visibility Single data: 8 rows, SHA-256 unchanged across install,
  old-app uninstall, and repeated migration.
- Restricted-user child: 1 row, SHA-256 unchanged.
- Pattern child: 1 row, SHA-256 unchanged.
- All three DocTypes transferred to module `AWHT Custom`.
- Old `cost_visibility` uninstalled **on that clone only**.
- 5 Frappe site-backed integration tests passed after the hand-off.
- Real Query Report and ReportView PDF bytes began with `%PDF`.
- Restricted user was denied raw Prepared Report File access.

Do not interpret the above as a live v15 compatibility or finance-policy test.
No positive-credit-policy branch records existed on the local snapshot, so
actual branch stock/receivables, POS, returns, concurrent posting, and the
original historical reports cannot be accepted from this evidence alone.

## Production Inventory

The workspace contains `awht_live_inventory.py`. It makes **HTTPS GET** API
requests only, prompts locally for the API key and secret without echoing
them, and writes a sanitized JSON report without credentials or business
rows. Run it from your own PowerShell session with an appropriately authorized
temporary API user (never paste a token into chat):

```powershell
wsl --cd /home/arslan/frappe-bench env/bin/python -B "/mnt/c/Users/Arslan/Documents/Default Project/awht_live_inventory.py" --url https://awht.metadaftr.com/ --output "/mnt/c/Users/Arslan/Documents/Default Project/awht_live_inventory.json"
```

The report inventories DocField/Custom Field/Property Setter names and types,
report roles/modes, selected record counts and read-only script metadata.
It does **not** collect credentials, customer records, bank details or raw
invoice/stock data. Also obtain `bench version` from the live bench and the
actual v15 patched-core diff for `salesman_price`, `old_price` and
`get_last_transfer_rate`; app directories alone cannot reproduce those
patches. If the live version is v15, this v16-targeted app must not be
installed until a v15 compatibility implementation and test site exist.

## Safe Cutover Order

1. Inventory live installed apps, fieldnames, permissions, role assignments,
   source-app usage, credit policy, report definitions, letterheads and queued
   jobs. Decide exact mappings/authorized behaviors with finance and users.
2. Reconcile all original features above, especially the two historical
   reports, production field aliases/core patch and active credit gate.
3. Rehearse on a **current production backup clone** with outbound traffic and
   scheduler disabled. Test all four app feature paths, permissions, returns,
   cancellation, concurrent transfers and PDF/print output. Snapshot complete
   Cost Visibility Single and child rows, reports, fields and role grants.
4. Take and restore-test a production database + site/files backup. Stop new
   requests and drain old queued `cost_visibility.*` jobs during maintenance.
5. Install `awht_custom` on the clone, verify all data/permissions and that
   the Cost Visibility DocTypes now belong to `AWHT Custom`. Do not uninstall
   the old app while any of its DocTypes still belong to its module.
6. Uninstall old apps only after verifying retained DocTypes/data, report
   ownership and effective hooks; build assets, migrate and repeat the full
   test matrix. Repeat on production only after explicit approval.

Do **not** run `uninstall-app awht_custom` as a rollback after it owns Cost
Visibility data: its uninstall could delete the transferred DocTypes. Restore
the reviewed database, site/files, code, app list and assets together under
maintenance if a cutover checkpoint fails. After new transactions are posted,
restoring a pre-cutover backup would lose those writes and needs a separate
reconciliation plan.

## Security and Accounting Boundaries

- Cost Visibility is a presentation filter, not field-level authorization for
  all APIs. Keep Frappe DocPerm, User Permission, print and File controls.
- Never grant unrestricted branded PDF merely because a report has a Desk
  button. PDF/QR/transfer-price endpoints enforce server-side permissions.
- Branch credit exposure currently covers SAR Bin stock plus positive SAR
  Sales Invoice outstanding and a prospective head-office transfer. Retention
  moved to separate Journal Entries, advances, pending POS activity and other
  writers may change real exposure independently. The shared policy lock
  serializes participating Stock Entry submissions, not every financial path.
- Never treat a Material Request preview as a reserved credit limit.

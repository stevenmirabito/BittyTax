# Plan: Per-Wallet Cost Basis for `US_INDIVIDUAL` (2025+)

Status: **implemented** (phases 1–6) on `2022-combined`; see §11 for where the implementation differs from this plan. Section 9 records the decisions.

## 1. What changed in the rules

| Source | What it requires | Effect on BittyTax |
|---|---|---|
| **Treas. Reg. §1.1012-1(j)** (final broker regs, TD 10000), for sales, dispositions and transfers on or after **2025-01-01** | Basis and holding period are identified **wallet by wallet / account by account**. Units in one wallet cannot be treated as sold from another. If you don't identify units, **FIFO applies inside that wallet**, ordered by the units' acquisition dates. Specific identification and standing orders are allowed. | The current universal FIFO pool is not allowed for 2025+ disposals. Transfers between wallets now affect which lots are in each wallet, so they must move lots, not be ignored. |
| **Rev. Proc. 2024-28** (transition safe harbor) | On 2025-01-01, the "unused basis" left over from the universal method may be **allocated to the units remaining in each wallet**, using a *specific unit allocation* or a *global allocation rule*. | The allocation is optional. Without one, each wallet's contents on 2025-01-01 come from **actual tracing** of the per-wallet history (§4), which is what this implementation does. |
| **Notice 2025-7** (2025) and **Notice 2026-20** (extends through **2026-12-31**) | For units **held by a custodial broker**, you can make adequate identification (specific ID **or a standing order**) in **your own books and records**, no later than the date and time of the sale, without telling the broker. Notice 2026-20 adds that **your books control even when the broker's 1099-DA shows something different**, and you must be able to reconcile the two. Units in self-custody already used your own records under §1.1012-1(j). | Without a recorded standing order or identification, disposals are **FIFO per wallet**. Standing orders and identifications only apply *going forward* from the date they are recorded (§3.2). The tool's lot ledger becomes the "books and records", so it has to be exportable. A 1099-DA reconciliation report is valuable. |
| **Form 8949 (2025+)** | Digital assets have their own boxes: **G/H/I** (short-term) and **J/K/L** (long-term), based on whether a 1099-DA was issued and whether basis was reported. Mandatory broker basis reporting starts with covered units acquired on or after 2026-01-01. | Reports need a short/long-term split, per-lot acquisition dates, and wallet metadata (broker or self custody, issues a 1099-DA). |
| **IRC §1015 / §1223(2)** (gifts received) | The donee takes the donor's basis and holding period. If the FMV at the gift date is below the donor's basis, the basis **for computing a loss** is that FMV, and a sale in between gives no gain or loss. Basis is increased for gift tax paid on appreciation (§1015(d)). | New lot attributes and input fields for `Gift-Received` (§5.3). |

**Note on the safe harbor:** some secondary sources read the specific-unit window as lasting until the earlier of the first 2025 sale or transfer from a wallet and the 2025 return due date (2026-10-15 with an extension). Any 2025 activity in a wallet would already have closed it for that wallet. This plan therefore implements **tracing only**. The allocation modes are deferred (§10).

## 2. How the current US calculator works (and why it can't simply be extended)

- `TaxCalculatorUS.calculate` (`bittytax/tax/us.py:48`) puts every crypto `Buy` acquisition and `Sell` disposal into one global list per asset. It then runs `TaxCalculator.match_sell(RULE_FIFO)` (`bittytax/tax/base.py:371`). **Wallet is ignored.**
- Deposits and withdrawals are not acquisitions or disposals. They go into `other_transactions` and are skipped in `process_holdings` (`base.py:489`) unless `transfers_include` is set. The two sides of a transfer are **separate, unlinked records** (`Withdrawal` in wallet A, `Deposit` in wallet B; `import_records.py:146`).
- Any sell left unmatched after FIFO falls through to `process_holdings`/`_subtract_tokens`. That code removes **average cost** from a per-asset `Holdings` pool (`base.py:518`) but still labels the result "First-In, First-Out". This is a latent bug.
- `_match_fifo` compares **dates only** (`us.py:95`). So a buy later on the same day can be matched to an earlier sell.
- `TaxEventCapitalGains` keeps one `acquisition_date` (`base.py:670`), with no short/long-term flag. The report groups by `te.wallet`, which is the wallet of the *sale*.
- `Gift-Sent` is in `Sell.DISPOSAL_TYPES` (`transactions.py`), so **US gifts sent are currently taxed as sales**. In the US, a gift is not a realization event for the donor. `Gift-Received` uses FMV as basis, which is wrong under §1015.
- Time zone is hard-coded to `Europe/London` (`config.py:17`). For US rules, that puts year boundaries and the **2025-01-01 cutover** in the wrong place for transactions near midnight.
- Year validation and the CGT estimate use the UK table `CG_DATA_INDIVIDUAL` (`bittytax.py:142`, `base.py:713`). For US runs, `--taxyear 2026` is rejected and the "estimate" applies UK allowances.
- Commit `0038712` changed the **shared** templates (`capital_gains.html`, `income.html`, `tax_report.html`) to the US layout and removed the UK estimate sections, so UK reports are currently US-styled.

**Conclusion:** the `match_sell`/`TaxRule` framework matches anonymous buys to sells. It has no concept of a lot that lives in a wallet and moves between wallets. The US calculator should move to an explicit **lot ledger**. The UK calculator (Section 104 pooling) and the UK templates go back to their pre-`0038712` behaviour.

## 3. Target design

### 3.1 Core model: lots in wallets

New module `bittytax/tax/lots.py`:

```python
class Lot:
    lot_id          # stable ID: "<asset>:<orig wallet>:<ISO timestamp>:<tid>" (+ split suffix)
    asset
    wallet          # current location
    quantity        # Decimal
    cost            # gain basis, USD, includes acquisition fees
    loss_basis      # None, or FMV-at-gift when it is below donor basis (§5.3); loss computations use this
    acquired        # holding-period start (tacks across transfers; donor's date for carryover gifts)
    origin_tid      # TID of the acquiring transaction
    covered         # acquired >= 2026-01-01 in a 1099-DA-issuing wallet (for 8949 box hints)
    history         # list of (timestamp, from_wallet, to_wallet) transfer hops

    def split(self, quantity) -> "Lot"   # proportional basis split (cost and loss_basis), as Buy.split_buy does today

class WalletLedger:
    lots: dict[(wallet, asset)] -> list[Lot]
    def add(lot)
    def remove(wallet, asset, quantity, at, selector) -> list[Lot]   # the lots consumed (split as needed)
    def move(from_wallet, to_wallet, asset, quantity, at, selector)  # remove, then re-add with wallet changed
    def snapshot(at) -> dict          # for the 2025-01-01 transition report and year-end holdings
```

The ledger supports two **modes**:
- **universal**: one bucket per asset. Used to compute pre-2025 gains exactly as reported before.
- **per-wallet**: one bucket per wallet and asset. Used for every disposal from 2025 on, and to trace wallet contents up to the cutover (§4).

### 3.2 Lot selection

`bittytax/tax/lot_selection.py`, with a single interface `select(lots, quantity, disposal) -> ordered lots`:

- `FIFO`: the default, and required by §1.1012-1(j) when nothing is identified. Orders lots by `acquired`, using the full timestamp, then by `lot_id`.
- `LIFO`, `HIFO`, `LOFO`: valid only as **standing orders** with an `effective_from` timestamp.
- `SPECIFIC`: explicit per-disposal identifications loaded from an identifications file (§3.5).

Resolution order for a disposal or transfer out of wallet W at time T:
1. A specific identification for this transaction, if one exists.
2. The standing order for W whose `effective_from` is at or before T, then the global standing order.
3. FIFO.

Without standing orders, all output is FIFO per wallet. **The tool must reject a standing order or identification recorded after the transactions it would govern.** Retroactive HIFO is not adequate identification.

Optional, outside the code: Notice 2026-20 still lets you record a standing order (for example HIFO) in your own records for broker-held units for **the rest of 2026**. If you do, you add it to the config with the real date you recorded it.

### 3.3 Transfers

Transfers now move lots between wallets, so they have to be linked:

- **Auto-matching** (`bittytax/tax/transfers.py`): match a `Withdrawal` (wallet A) to a `Deposit` (wallet B, B ≠ A). Matching conditions:
  - same asset;
  - deposit time within `transfer_match_window` (default 24 h) after the withdrawal;
  - deposit quantity ≤ withdrawal quantity, and within `transfer_match_tolerance`.
  
  Pick the closest match by time, then by quantity.
- **Explicit linking**: a `transfer:<id>` token in the `Note` column overrides auto-matching. This avoids changing the 13-column record format the conversion tool emits.
- Lots are selected out of A using §3.2. Transfers count as "transfers" under §1.1012-1(j), so the same identification rules apply. The lots are then re-added to B with `acquired`, `cost` and `loss_basis` unchanged.
- **Unmatched withdrawal**: an error in US mode, with a message telling the user to record it as `Gift-Sent`/`Spend`/etc. or add the deposit. `--skipint` downgrades it to a warning; the lots then leave the ledger with no tax event.
- **Unmatched deposit**: creates lots with unknown basis. This is an error by default. The fix is a `Buy Value` (the basis), with the acquisition date taken from an `acquired:<ISO date>` note token. Otherwise it falls back to zero basis and the deposit date, with a warning.
- **Tracing (§4) requires complete transfer history**, so a report of unmatched transfers is the first thing to look at for 2025.

### 3.4 Fees

- **Trade and spend fees:** keep the current allocation (`transactions.py:31`). For a buy, the fee is added to the lot's basis; for a sell, it reduces the amount realized. Crypto-denominated fees consume lots from the **same wallet** via §3.2.
- **Transfer (network) fees paid in crypto: treat them as taxable disposals by default.** The fee units are selected from the sending wallet's lots, with proceeds equal to FMV and a gain or loss against their basis. This is today's `transfer_fee_disposal: True` default, now with per-wallet lot selection.

  *Why not something else:* the final §1.1012-1 regulations allocate digital-asset transaction costs only for **sales and exchanges**. The preamble left fees on transfers between your own wallets unaddressed. The alternative, a non-taxable removal with the fee units' basis added to the transferred lots, has no direct authority, though it is usually small in dollar terms. Disposal treatment is the conservative and most common position, and changing it between years would make them inconsistent. The cost of the fee itself is not deductible for an individual: it is a §212 investment expense, and §67(g) suspends that deduction. Keep `transfer_fee_disposal: False` available, but don't make it the default. Confirm with a tax professional if the amounts are material.

### 3.5 New inputs

All inputs are optional. Without them, the behaviour is "trace per-wallet history; FIFO per wallet from 2025".

**`~/.bittytax/bittytax.conf`** (new keys):

```yaml
us_timezone: America/Los_Angeles        # used for all timestamps and year boundaries under --taxrules US_INDIVIDUAL
us_wallet_cutover: 2025-01-01           # per-wallet rules apply to transactions on/after this local date
transfer_match_window: 24h
transfer_match_tolerance: 0.5%
us_standing_orders: []                  # e.g. { wallet: 'Coinbase', method: HIFO, effective_from: 2026-10-08T00:00:00 }
us_identifications_file: ~                # optional specific-ID file
wallets:                                # metadata for 8949 boxes and 1099-DA reconciliation
  Coinbase: { custody: broker, issues_1099da: true }
  Ledger:   { custody: self }
```

**`identifications.csv`**: `Wallet, Timestamp, Asset, Quantity, Lot ID, Recorded At`. The tool rejects any row whose `Recorded At` is after the disposal.

**Gift-received inputs**, as `Note` tokens on the `Gift-Received` row, consistent with `transfer:`/`acquired:`:
- `donor_basis:<USD>` (required in US mode; the run errors if it is missing)
- `donor_acquired:<ISO date>` (required)
- `gift_tax_paid:<USD>` (optional)

`Buy Value` keeps meaning FMV at the gift date, as today.

## 4. Transition at the cutover (2025-01-01 00:00 America/Los_Angeles)

There is no safe harbor allocation, so the 2025-01-01 contents of each wallet are the lots actually traced into it.

1. **Pre-2025 gains:** replay every pre-cutover transaction in **universal FIFO** mode, exactly as today, so filed years don't change (regression test, §7). These figures go only to pre-2025 tax years.
2. **Tracing pass:** replay the same pre-cutover history in **per-wallet FIFO** mode, with transfers moving lots and disposals consuming lots in their own wallet. The resulting lots per (wallet, asset) on 2025-01-01 become the opening state for 2025. Gains from this pass are thrown away.
3. **Reconcile the two passes** and print the result:
   - Per asset, the total remaining units must be equal in both passes (hard error).
   - Remaining basis will usually **differ**, because universal FIFO and per-wallet FIFO consumed different lots before 2025. Report the difference per asset as "basis shift from tracing". It is information, not an error. It is the gap the safe harbor would have smoothed. Without an allocation, the traced figure is the correct starting basis. A small amount of basis can drop out or be counted twice between the pre-2025 and 2025+ computations; the report makes this visible so it can be reviewed with a preparer.
4. Per (wallet, asset), the traced units must equal the audit balance from `AuditRecords` (`audit.py:13`). If they don't, transfer data is missing (§3.3).
5. Emit a **Transition Report** (PDF section + CSV): each wallet, asset and lot with quantity, basis, acquired date and transfer history, plus the basis shift table. This documents the opening per-wallet books.

## 5. Calculator changes

### 5.1 `bittytax/tax/us.py`: rewrite `TaxCalculatorUS.calculate`

```text
prepare_transactions()          # keep deposits/withdrawals/fees in time order
link_transfers()                # §3.3
pre  = replay(txns < cutover, WalletLedger(universal))   -> tax events for pre-2025 years
open = replay(txns < cutover, WalletLedger(per_wallet))  -> opening state, events discarded
reconcile(pre, open)                                      # §4.3–4.4
post = replay(txns >= cutover, open)                      -> tax events for 2025+
```

`replay` handles each transaction in (timestamp, tid) order:

| Transaction | Action |
|---|---|
| `Buy` acquisition | `ledger.add(Lot(...))`. Income types use FMV as basis. `Gift-Received` follows §5.3. |
| `Sell` disposal (`Trade`, `Spend`) | `ledger.remove(...)`, then one `TaxEventCapitalGains` per consumed lot. |
| `Gift-Sent` | `ledger.remove(...)` with **no tax event**. The removed lots are listed in a "gifts sent" schedule (basis and acquired date for the recipient, and for the Form 709 threshold). |
| `Gift-Spouse` | Same as `Gift-Sent` (§1041). |
| `Charity-Sent` | Removes lots, no gain. Listed in a "charitable contributions" schedule with FMV, basis and term, because a deduction is limited to basis for short-term units. |
| `Lost` | Removes lots with no gain event. `lost_buyback` is forced off in US mode. A casualty or theft loss is generally not deductible for individuals after 2017, so the run prints a warning. |
| Linked `Withdrawal` | `ledger.move(...)`, plus the fee disposal (§3.4). |
| Linked `Deposit` | No-op; the withdrawal already moved the lots. |
| Unlinked `Deposit` / `Withdrawal` | Rules in §3.3. |

- Stop using `match_sell`/`RULE_FIFO`/`Holdings` for US rules. Leave them in place for the UK.
- Remove the average-cost fallback. Selling more than a wallet holds becomes an integrity error that names the wallet, asset and TID.
- Override `NO_GAIN_NO_LOSS_TYPES` and `Sell.disposal` per regime. Today the disposal flag is decided at import (`Sell.DISPOSAL_TYPES`), so the US calculator needs its own mapping and must not rely on that flag.

### 5.2 `TaxEventCapitalGains` (`base.py:660`)

Add:
- `lot_id`
- `acquisition_date` (always set for US)
- `holding_period`: `SHORT`/`LONG`. Long-term means held **more than one year**. The holding period starts the day after `acquired` and tacks across transfers.
- `wallet` (the disposing wallet)
- `form_8949_box`: G–L for 2025+, C/F before 2025
- `identification`: `FIFO`, `standing:<method>` or `specific`
- `gift_rule`: `None`, `gain`, `loss` or `no_gain_no_loss` (§5.3)

Put the US-specific fields on a subclass `TaxEventCapitalGainsUS` so that the UK event stays unchanged.

### 5.3 Gifts received (§1015, §1223(2))

On `Gift-Received`, create a lot as follows. `FMV` is the row's Buy Value; `DB` is `donor_basis` plus the §1015(d) gift tax adjustment.

- **Gift tax adjustment (§1015(d)(6)):** `DB += gift_tax_paid × max(FMV − donor_basis, 0) / FMV`.
- **If `FMV ≥ DB`:** `cost = DB`, `loss_basis = None`, and `acquired = donor_acquired`, so the holding period tacks.
- **If `FMV < DB`** (dual basis): `cost = DB` and `loss_basis = FMV`. The lot keeps both possible holding-period start dates, `donor_acquired` and the gift date.

On disposal, split proportionally like any other lot. For a lot with `loss_basis` set, compare the amount realized (AR) to the two bases:
- `AR > cost`: gain = AR − cost. Holding period from `donor_acquired`.
- `AR < loss_basis`: loss = AR − loss_basis. Holding period from the **gift date** (§1223(2) applies only when the donor's basis is used).
- Otherwise: no gain or loss. Report the event with `gift_rule = no_gain_no_loss`.

`Gift-Received` stays out of income (as today) and is not part of the per-wallet basis-shift check.

### 5.4 Tax years and time zone

- `config.TZ_LOCAL` becomes configurable. When `--taxrules US_INDIVIDUAL` is set (`bittytax.py:110`), set it from `us_timezone` (`America/Los_Angeles`), and use it as the default zone for naive timestamps during import. Import currently runs **before** the tax rules are applied, so the order in `main()` has to change. Otherwise naive timestamps would be parsed as London time.
- Add US year handling so that `validate_year` and `do_each_tax_year` accept 2025, 2026 and later for US rules (`bittytax.py:142`, `:247`), independent of `CG_DATA_INDIVIDUAL`. Drop the CGT estimate for US.

### 5.5 Integrity check

`AuditRecords.compare_pools` currently compares per-asset totals. For US, compare **per (wallet, asset)** at every year end and at the cutover: ledger quantity vs audit quantity.

## 6. Reporting: separate US templates

### 6.1 Template split
- Move the US layout introduced in `0038712` into **`bittytax/templates/us/`**: `tax_report.html`, `capital_gains.html`, `income.html`, `holdings.html`, plus new `transition.html`, `gifts.html` and `form_8949.html`.
- **Restore the shared/UK templates** (`capital_gains.html`, `income.html`, `tax_report.html`) to their pre-`0038712` content, including the CGT/CT estimate sections and the tax year heading. Do this by reverting those template hunks, not the `base.py` wallet tracking, which both regimes can use.
- `ReportPdf` (`report.py:24`) picks the template root by `args.tax_rules`. Do this with a `PrefixLoader` or a second `PackageLoader`, so that `{% include %}` resolves within the regime's directory.
- `ReportLog` (`report.py:121`) gets a `ReportLogUS` subclass that overrides `capital_gains`, `income` and `holdings`, so the terminal output matches the US PDF.

### 6.2 US report contents
1. **Capital gains:** Short-term, then Long-term, then by wallet. Columns follow Form 8949 (description, date acquired, date sold, proceeds, basis, adjustment code/amount, gain), plus lot ID and identification method. Subtotals per wallet and per 8949 box.
2. **Form 8949 CSV export** (`--export-8949`): one row per lot disposal with the box code.
3. **Income:** as the current US layout (by wallet and type).
4. **Holdings:** wallet → asset → open lots (quantity, basis, acquired, term at year end).
5. **Gifts and donations:** gifts received (with dual-basis detail), gifts sent, and charitable contributions.
6. **Transition Report** (§4.5): only when the run spans the cutover.
7. **Lot ledger export** (`--export-lots`): the full history of every lot (adds, splits, moves, disposals) with the identification used. Under Notice 2026-20, this is the evidence that your books and records control.
8. **1099-DA reconciliation** (phase 6): import a broker 1099-DA CSV and match rows to disposals by wallet, asset, date and proceeds. List differences in basis or acquisition date, and explain them.

## 7. Testing

The repo has no automated tests today; `test/` only holds HMRC example workbooks. Add `pytest` with small CSV fixtures under `test/us/`:

| Test | Asserts |
|---|---|
| US regression | Pre-2025 gains on an existing dataset are identical before and after the refactor. Capture golden output from `9110f88` first. |
| UK regression | The UK calculator gives the same numbers, and the UK templates render the restored estimate sections. |
| Per-wallet FIFO | Buy 1 BTC @10k in A (2023) and 1 BTC @50k in B (2024); sell 1 from B in 2025. Basis must be 50k (universal FIFO would give 10k). |
| Tracing transition | Opening lots per wallet match hand-computed values. Units reconcile across both passes, and the basis-shift table is correct. |
| Transfer carryover | A lot moved A→B keeps its basis and acquired date. Long-term status survives the transfer. |
| Transfer fee | Fee units come from the sender's lots at FMV with a gain or loss. The deposit is short by the fee. |
| Unmatched transfers | Error vs `--skipint` warning. |
| Cutover / time zone | Transactions at 2024-12-31 23:59 and 2025-01-01 00:01 **PT** fall on the correct side. A naive timestamp is parsed as PT. |
| Standing order | A HIFO order effective mid-year applies only to later sales. One recorded after a sale is rejected. |
| Holding period | Exactly 1 year is short-term; 1 year + 1 day is long-term. |
| Same-day ordering | A buy at 15:00 is not matched to a sell at 09:00 on the same day. |
| Gift received | Carryover basis with a tacked holding period. Dual-basis sales: above donor basis (gain), below FMV (loss, with the holding period from the gift date), and in between (no gain or loss). Gift tax adjustment. Missing `donor_basis` errors. |
| Gift sent / charity / lost | No gain events. Lots are removed. The schedules are populated. |
| 8949 boxes | Box selection for broker/self custody, covered/noncovered, and pre/post 2025. |

## 8. Phasing

1. **Foundations:** template split and UK restore (§6.1); configurable time zone with PT import (§5.4); US year handling; `Lot`/`WalletLedger`; US calculator rewritten on the ledger in universal mode only. Ship when both regression tests pass.
2. **Per-wallet + transfers:** transfer linking, unmatched-transfer report, tracing transition and Transition Report, per-wallet FIFO from 2025, fee disposals, per-wallet integrity check.
3. **US disposal semantics + holding period:** `Gift-Sent`/`Gift-Spouse`/`Charity-Sent`/`Lost` fixes, short/long term, 8949 boxes, US templates, `--export-8949`.
4. **Gifts received:** dual-basis and gift tax adjustment, plus the gifts schedule.
5. **Identification + records:** standing orders and specific IDs (prospective only), `--export-lots`.
6. **1099-DA reconciliation.**

Phases 1–3 are enough to produce a correct 2025 return for records without an allocation or standing orders, with transfer fees treated as disposals.

## 9. Decisions

| # | Decision | Effect |
|---|---|---|
| D1 | Transition by tracing | Rev. Proc. 2024-28 allocation modes are deferred (§10). |
| D2 | Identification only applies going forward | FIFO per wallet unless a standing order or identification was recorded before the sale. |
| D3 | Crypto transfer fees are disposals by default | §3.4 explains why it is the recommended treatment. |
| D4 | `us_timezone: America/Los_Angeles` | §5.4 |
| D5 | Gifts received use carryover and dual basis | §5.3, phase 4 |
| D6 | Separate US report templates | §6.1; UK templates restored |

## 10. Deferred

- Rev. Proc. 2024-28 `global_allocation`/`specific_allocation` transition modes (deferred).
- Inherited assets (§1014 step-up). Wash sales do not apply to digital assets under current law, so there is nothing to implement for them.
- Enforcing broker-communicated identification for broker-held units after the Notice 2026-20 relief ends (2027+), unless the relief is extended again.

## 11. Implementation notes

What was built differs from the plan above in these places:

- **Naive timestamps stay UTC** (§5.4). The plan said to read timestamps with no zone as Pacific time. Conversion-tool output and existing records rely on UTC, so only local time (year boundaries, holding periods, the cutover) uses `us_timezone`. `Config.set_tax_rules` still runs before import, because records are converted to local time as they are created.
- **Config keys** (§3.5): `transfer_match_window_hours` (number) and `transfer_match_tolerance` (fraction, default 0.005), instead of `24h` / `0.5%`. All US keys are documented in `bittytax/config/bittytax.conf`.
- **Lot IDs** are `<asset>:<wallet acquired in>:<UTC timestamp>`, with `#n` added only for acquisitions in the same second. They don't include the TID, so adding other records doesn't change them. `--export-lots` lists them for specific identification.
- **Transitions are traced only**: the allocation modes stay deferred (§10). The tracing pass reports its own shortfalls, labelled `[tracing]`, separately from the universal pass (`[universal]`) and the per-wallet pass (`[per-wallet]`).
- **Lot selection only applies from the cutover.** Before it, both passes use FIFO. Fragments of one lot taken in a single disposal are reported as one Form 8949 row when they fall in the same box.
- **The workbook is the single source of truth.** Standing orders, specific identifications and 1099-DA rows live on workbook sheets (`Standing Orders`, `Identifications`, `1099-DA <broker>`, one sheet per broker covering all years with a `Form Year` column), not in config or separate files. A `Timestamp` names a transaction and is read as UTC when it has no zone, like the transaction sheets. `Recorded At` and `Effective From` are read in local time.
- **1099-DA rows set Form 8949 columns (d)–(g)** (a refinement of §6.2 item 8). Matched sales take the broker's proceeds, and the broker's basis when it was reported to the IRS. Column (g) carries the correction so that (h) equals the gain from your records. Codes are B (basis), E (selling costs) and O (other, flagged for review). The box comes from the form: G/J or H/K, with I/L when unreported. The covered-lot guess and `--reconcile-1099da` were removed. The output is framed as an Exception 2 statement attached to Form 8949.
- **`transfers_include`** has no effect on US lot tracking, since transfers always move lots. If it is set, the importer still treats transfer fees as non-disposals.
- **Rebates and referrals have their own types** (decision after validating real data). The conversion tool used to record card rebates and rewards as `Gift-Received` to keep them out of income. The fork now has upstream v0.6.0's `Cashback`, `Fee-Rebate` and `Referral` types. Cashback and fee rebates are received at FMV basis, aren't income, and are listed in the US report. Referral is income under US rules. Under UK rules all three are treated like `Gift-Received` until upstream's UK treatment is checked. The Crypto.com parser emits `Cashback` for card cashback and reimbursements, and `Referral` for referral bonuses. `Gift-Received` without donor details fails the integrity check again.
- **Transfer clock skew** (found in real data): a deposit may be recorded up to `transfer_match_skew_minutes` (default 60) before its withdrawal.
- **Integrity check (US)**: lot shortfalls, unlinked transfers, gifts without donor details and per-wallet audit mismatches all fail the check. `--skipint` reports them and continues.

## Sources

- Notice 2026-20 summaries: [Current Federal Tax Developments](https://www.currentfederaltaxdevelopments.com/blog/2026/3/18/extension-of-temporary-relief-for-digital-asset-identification-a-technical-review-of-notice-2026-20), [Sovos](https://sovos.com/regulatory-updates/trr/irs-notice-2026-20/), [Camuso CPA](https://camusocpa.com/irs-notice-2026-20/)
- Rev. Proc. 2024-28: [IRS PDF](https://www.irs.gov/pub/irs-drop/rp-24-28.pdf), [The Tax Adviser](https://www.thetaxadviser.com/news/2024/oct/universal-accounting-for-digital-assets-concludes-but-safe-harbor-available.html)
- Wallet-by-wallet rule, 1099-DA, deadlines: [Forvis Mazars (Sept 2026)](https://www.forvismazars.us/forsights/2026/09/form-1099-da-digital-asset-basis-what-to-know)
- Form 8949 digital-asset boxes: [Instructions for Form 8949 (2025)](https://irs.gov/instructions/i8949)

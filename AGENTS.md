# AGENTS.md: Filling the records workbook

These instructions are for agents (and people) entering data into the records workbook, normally
`BittyTax_Records.xlsx`. The workbook is the **single source of truth**. `bittytax` reads
nothing else about your history: no separate 1099-DA file, no identification file. Under
Notice 2026-20 your own books and records decide which units were sold, so the workbook *is*
those records. Treat every edit as an edit to tax records.

These instructions cover the US tax rules (`--taxrules US_INDIVIDUAL`). The [README](README.md)
describes the transaction format in full. This file is the operating guide and lists what the
US rules add.

## Ground rules

1. **Never invent data.** Every row must come from a source document: an exchange export, a
   wallet or block-explorer history, a broker Form 1099-DA, or a record the user dictates. If a
   value is unknown, leave it out (values are looked up) or ask. Don't guess.
2. **Never backdate.** `Recorded At` (Identifications) and `Effective From` (Standing Orders)
   must be when the user *actually* made that decision. An identification or standing order
   only counts if it was recorded no later than the sale (Treas. Reg. 1.1012-1(j)(3)). If the
   user decides something today about a past sale, it can't be recorded for that sale.
3. **Don't make the integrity check pass by fabricating rows.** An unmatched withdrawal or
   deposit means records are missing. Find the real counterpart, or ask the user what happened.
   Don't add a balancing Deposit, Spend or Gift.
4. **Keep wallet names consistent.** The `Wallet` value is the identity of a wallet everywhere:
   transaction sheets, `Identifications`, `Standing Orders` and `1099-DA` sheets, plus the
   `wallets:` setting in `~/.bittytax/bittytax.conf`. `Coinbase` and `coinbase` are different
   wallets. One wallet means one exchange account or one self-custody wallet.
5. **Keep source data.** Don't delete rows to fix balances. Use a worksheet whose name starts
   with `--` (it is skipped on import) to park raw or reference data.
6. **Re-run and check after every batch of edits** (see [Checking your work](#checking-your-work)).

## Workbook layout

| Worksheet name | Contents | Read as |
| --- | --- | --- |
| anything else (e.g. `Coinbase`, `Ledger`) | Transaction records | Transactions |
| `Standing Orders` | Lot selection rules | Special sheet |
| `Identifications` | Units identified for specific sales | Special sheet |
| `1099-DA <broker>` (e.g. `1099-DA Coinbase`) | One broker's Form 1099-DA rows, **all years** | Special sheet |
| `--<anything>` | Notes, raw data | Skipped |

- Every sheet has a **header row**. Data starts on row 2.
- Special sheets are recognised by name (case-insensitive). Their header must match the columns
  below **in this order**. Extra columns after the required ones are ignored, so you can add
  your own notes columns on the right.
- Use **one `1099-DA` sheet per broker** and add each year's form to it, with a `Form Year`
  column. Don't create a sheet per year.
- `bittytax_conv <exports...>` generates the transaction sheets from exchange and wallet
  exports. Pass it the original raw files, and review its output against the raw data.

## Transaction sheets

Columns (the first 13 are read):

`Type | Buy Quantity | Buy Asset | Buy Value | Sell Quantity | Sell Asset | Sell Value | Fee Quantity | Fee Asset | Fee Value | Wallet | Timestamp | Note`

Values are in the local currency, which must be `USD` for US rules: set
`local_currency: 'USD'` in `~/.bittytax/bittytax.conf`.

### Which type to use

| Event | Type | Notes |
| --- | --- | --- |
| Buy crypto with USD | `Trade` | Buy = crypto, Sell = USD. Sell Value not needed. |
| Sell crypto for USD | `Trade` | Buy = USD, Sell = crypto. |
| Swap crypto for crypto | `Trade` | Give at least one side's USD value if the exchange reports it. |
| Pay for goods or services with crypto | `Spend` | Sell Value = FMV of what was received. |
| Move coins between **your own** wallets | `Withdrawal` + `Deposit` | One row in each wallet, see [Transfers](#transfers). |
| Staking, mining, interest, rewards | `Staking` / `Mining` / `Interest` / `Income` | Buy Value = FMV when received (ordinary income). |
| Airdrop, or new coins from a hard fork | `Airdrop` | Buy Value = FMV when received (income, Rev. Rul. 2019-24). **Don't** use `Gift-Received` for forks. |
| Coins received as a gift from a person | `Gift-Received` | Needs donor details in the Note, see [Gifts received](#gifts-received). |
| Card cashback, subscription or purchase rebates | `Cashback` | Not income. Basis = FMV when received (Buy Value). |
| Exchange fee rebates | `Fee-Rebate` | Not income. Basis = FMV when received. |
| Referral, sign-up or promotional bonuses | `Referral` | Ordinary income at FMV. **Ask the user first**: whether a bonus is income is the user's (or their preparer's) decision. |
| Coins given as a gift | `Gift-Sent` | Not a sale. Sell Value = FMV (shown on the gift schedule). |
| Coins given to a spouse | `Gift-Spouse` | Not a sale (IRC 1041). |
| Donation to a charity | `Charity-Sent` | Not a sale. Sell Value = FMV (deduction schedule). |
| Coins lost or stolen | `Lost` | Removes the lots, no capital loss. |
| Exchange sweeps dust | `Spend` | Sell Value `0`. |

**Never** use `Deposit` or `Withdrawal` for coins going to or coming from **someone else**.
Those are `Gift-*`, `Spend`, `Trade` or income types.

### Field rules

- **Timestamps:** use the exact time from the source. An Excel date/time cell is read as
  **UTC**. Text must carry a zone, e.g. `2025-03-01T12:00:00Z` or
  `2025-03-01T04:00:00-08:00` (`UTC`, `GMT` and `BST` abbreviations also work). Text with no
  zone is read as UTC. The tax year, holding period and the 2025-01-01 per-wallet cutover are
  then worked out in Pacific time.
- **Fees** go in the Fee columns, never netted silently:
  - Fee in the *sold* asset: Sell Quantity is the **net** amount (after the fee).
  - Fee in the *bought* asset: Buy Quantity is the **gross** amount (before the fee).
  - `Withdrawal`: quantity is the **net** amount sent. The network fee goes in the Fee columns.
  - `Deposit`: quantity is the **gross** amount received.
  - A crypto fee is a disposal of that crypto (a small gain or loss). Keep it that way.
- **Values:** leave Buy/Sell/Fee Value blank to have them looked up. Fill them in when the
  source states a USD value, and **always** fill in the USD side of a trade.
- **Quantities:** must be positive. Use full precision, never rounded.
- **Assets:** use one symbol per asset throughout, matching the price data source
  (`bittytax_price` can check).

### Transfers

A transfer between your own wallets is a `Withdrawal` row in the sending wallet plus a
`Deposit` row in the receiving wallet. From 2025 lots **move with the coins**, keeping their
basis and acquisition date. Linking the two rows matters:

- **Automatic linking** pairs a Withdrawal with a Deposit when all of these hold:
  - same asset, different wallet;
  - the Deposit is at or after the Withdrawal, within 24 hours (`transfer_match_window_hours`);
  - the Deposit quantity is equal to the Withdrawal quantity, or smaller by up to 0.5%
    (`transfer_match_tolerance`).
- **Explicit linking:** when auto-linking would pick the wrong pair (several similar transfers,
  slow arrivals), put the same `transfer:<id>` token in the Note of **both** rows, e.g.
  `transfer:cb-ledger-0042`. The token must tag exactly one Withdrawal and one Deposit.
- **Coins arriving from outside your records** (a wallet you never tracked) need a Deposit with
  its basis declared: Buy Value = the original **cost**, not today's value, plus the note
  token `acquired:<date>` (e.g. `acquired:2020-05-01`). Only do this from real records of the
  original purchase. Otherwise ask the user.
- If the Deposit is short of the Withdrawal and no fee was recorded, the difference is removed
  without a gain and a warning is printed. Record the real network fee in the Fee columns
  instead.

### Gifts received

Gifts carry over the **donor's** basis and holding period. Put these tokens in the Note
(the order doesn't matter, other text is allowed around them):

| Token | Required | Meaning |
| --- | --- | --- |
| `donor_basis:<USD>` | yes | Donor's adjusted basis for the units given |
| `donor_acquired:<date>` | yes | When the donor acquired them |
| `gift_tax_paid:<USD>` | no | Gift tax the donor paid for this gift |

Buy Value is the **FMV on the date of the gift**. The tool applies the dual-basis rule when FMV
was below the donor's basis. Example note: `from Mom donor_basis:1200 donor_acquired:2019-04-02`.

A `Gift-Received` without both tokens fails the integrity check, and is then given FMV basis.
Rebates and rewards are **not** gifts: record them as `Cashback` (or `Fee-Rebate`) and bonuses
as `Referral`. Never add made-up donor tokens to make a row pass.

## `Standing Orders` sheet

Which lots to sell first when the user didn't identify specific units. Without an order, FIFO
within the wallet applies.

| Wallet | Method | Effective From |
| --- | --- | --- |
| `Coinbase` | `HIFO` | `2026-10-08 09:30` |
| `*` | `FIFO` | `2025-01-01` |

- **Wallet:** a wallet name, or `*` (or blank) for every wallet. A wallet's own order beats `*`.
- **Method:** `FIFO`, `LIFO`, `HIFO` or `LOFO`.
- **Effective From:** when the user recorded the order. **Local time** unless a zone is given.
  The order governs sales from this moment on, and the latest order in force wins. Add a new
  row to change an order. Never edit or backdate an old row.
- Orders only apply from 2025. Earlier years are always FIFO across all wallets.
- For **broker-held** wallets, recording orders in your own records is allowed through
  2026-12-31 (Notice 2026-20). From 2027 the broker must be told, and the tool warns.

## `Identifications` sheet

Specific units chosen for a sale or transfer, recorded **before** it happened.

| Wallet | Timestamp | Asset | Quantity | Lot ID | Recorded At |
| --- | --- | --- | --- | --- | --- |
| `Coinbase` | `2026-11-03T18:22:05Z` | `BTC` | `0.25` | `BTC:Coinbase:2024-06-01T18:00:00Z` | `2026-11-03 10:15` |

- **Wallet, Timestamp, Asset:** must exactly match the sale's (or Withdrawal's) transaction
  row. Timestamp follows the transaction rules: a cell, or text with no zone, is UTC.
- **Quantity:** units taken from that lot. Use several rows to take from several lots. Any
  quantity not covered falls back to the standing order, or FIFO.
- **Lot ID:** copy it from `bittytax ... --export-lots`. The format is
  `<asset>:<wallet first acquired in>:<UTC acquisition time>`, with a `#2` suffix for a second
  acquisition in the same second. It stays the same when other rows are added.
- **Recorded At:** when the user made the identification, in **local time** unless a zone is
  given. If it's after the transaction, the identification is rejected.

## `1099-DA <broker>` sheets

Transcribe each Form 1099-DA the broker issues, **one row per sale as the broker reported it**.
Add new years to the same sheet.

| Form Year | Wallet | Asset | Date Sold | Quantity | Proceeds | Cost Basis | Date Acquired | Term | Basis Reported |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2026` | `Coinbase` | `ETH` | `2026-01-15` | `3` | `9000.00` | `4200.00` | `2021-03-05` | `Long` | `Yes` |
| `2025` | `Coinbase` | `BTC` | `2025-06-01` | `0.5` | `50000.00` | | `VARIOUS` | | `No` |

- **Form Year:** the tax year printed on the form (it can differ from `Date Sold` for
  late-evening Dec 31 sales). When the broker issues a **corrected** form, replace that form
  year's rows.
- **Wallet:** the wallet name used in the transaction sheets for that broker account.
- **Asset:** the same symbol as in the transaction sheets, not the broker's display name.
- **Date Sold, Quantity, Proceeds:** exactly as printed. Proceeds as reported (net of selling
  costs on a 1099-DA). Don't adjust them to match your records. Differences are what the
  reconciliation is for.
- **Cost Basis** (box 1g) and **Date Acquired:** as printed, or blank (`VARIOUS` is accepted
  for the date).
- **Term:** `Short`/`Long` if the form says, otherwise blank.
- **Basis Reported:** `Yes` if the form shows basis was reported to the IRS, otherwise `No`.
  This decides the Form 8949 box (G/J vs H/K), and whether the broker's basis is used in
  column (e) and corrected in column (g).
- If a wallet issues 1099-DAs, set `issues_1099da: True` for it under `wallets:` in the
  config, so a year with no rows gives a warning.

## Config prerequisites (`~/.bittytax/bittytax.conf`)

```yaml
local_currency: 'USD'
date_format: '%m/%d/%Y'
us_timezone: 'America/Los_Angeles'
wallets: {
    'Coinbase': {custody: broker, issues_1099da: True},
    'Ledger': {custody: self},
    }
us_taxpayer_name: ''     # optional, printed on the Form 8949 statement
us_taxpayer_ssn: ''      # optional, leave blank to fill in by hand
```

Don't put Identifications, Standing Orders or 1099-DA data in the config. They belong in the
workbook.

## Checking your work

```console
bittytax BittyTax_Records.xlsx --taxrules US_INDIVIDUAL --nopdf
bittytax BittyTax_Records.xlsx --taxrules US_INDIVIDUAL --export-lots     # lot IDs and lot history
bittytax BittyTax_Records.xlsx --taxrules US_INDIVIDUAL --export-8949     # Form 8949 rows
bittytax BittyTax_Records.xlsx --taxrules US_INDIVIDUAL                   # PDF report
```

The import must report `failure=0`, and the integrity check must pass. Read each `ERROR` and
`WARNING` and fix the **records**, not the symptom:

| Message | Usual cause | Fix |
| --- | --- | --- |
| `'<sheet>' row N: ...` | Bad value or header on a special sheet | Correct that cell or the header |
| `Withdrawal ... has no matching Deposit` | Missing Deposit, or coins went to someone else | Add the real Deposit, or retype as Gift-Sent/Spend; or link with `transfer:<id>` |
| `Deposit ... has no matching Withdrawal` | Missing Withdrawal, or coins from outside | Add the Withdrawal, or declare basis (`acquired:`) from real records |
| `Insufficient <asset> in <wallet>` | Missing acquisition or transfer in, or wrong wallet name | Find the missing rows, check the Wallet spelling |
| `<wallet>:<asset> audit balance X, lots held Y` | Same as above | Same as above |
| `held at the cutover differs between the universal and traced methods` | Unlinked transfers before 2025 | Link or complete pre-2025 transfers |
| `Gift-Received ... needs donor_basis ... and donor_acquired` | Gift without donor details, or a rebate/bonus recorded as a gift | Add the donor tokens for a real gift; retype rebates as `Cashback`; ask the user about bonuses |
| `Identification ... was recorded after the transaction` | Backdated or late identification | Leave the sale to FIFO or the standing order. **Don't** change Recorded At |
| `1099-DA sale ... isn't in your records` | Missing or misdated sale, or wrong Wallet/Asset on the sheet | Find the sale; check the transcription |
| Reconciliation `Review (O)` | Proceeds differ unexplained | Check the sale value and fees in the transaction row |
| Reconciliation `Adjusted (B)` / `(E)` | Expected: basis or selling-cost differences | No fix needed. Confirm the note's explanation makes sense |
| `Price for X on DATE is not available` | No price source | Fill in the Value from the source document |

`--skipint` lets a report run despite failures. Use it only to inspect output, never for a
report that will be filed.

## Things to ask the user rather than decide

- Where coins went or came from when there's no matching record.
- Whether a transfer was to the user's own wallet or to someone else.
- Donor basis and acquisition date for gifts.
- Any standing order or identification, and when it was really recorded.
- Which wallet name a broker account maps to, when it's ambiguous.

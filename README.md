# NEPSE Floor Sheet CSV automation

GitHub Actions workflow that requests date-specific floor sheets from the NEPSE official API through the open-source `NepseUnofficialApi` client, then writes a fixed 14-column CSV.

## Important source limitation

The upstream client exposes:
- `getFloorSheet()` for the current market-wide floor sheet.
- `getFloorSheetOf(symbol, business_date)` for a specific security and date.

Therefore, this project iterates the official security list and calls the per-security historical method for each date. The script does **not** claim that a date is complete if most symbol calls fail. Empty results are logged as `EMPTY_OR_NON_TRADING`, not written as an empty CSV. Check the NEPSE holiday calendar and rerun if an expected trading day is empty.

The upstream project has documented an NEPSE TLS certificate-chain issue. This starter does not silently turn off TLS verification. If GitHub Actions reports a certificate verification failure, investigate the current certificate chain before deliberately changing TLS verification.

## Output

`data/floorsheet/YYYY/YYYY_MM_DD_floorsheet.csv`

Columns, in this exact order:

1. contractId
2. stockSymbol
3. buyerMemberId
4. sellerMemberId
5. contractQuantity
6. contractRate
7. contractAmount
8. businessDate
9. tradeBookId
10. stockId
11. buyerBrokerName
12. sellerBrokerName
13. tradeTime
14. securityName

`data/floorsheet/status.csv` records SUCCESS, SUCCESS_WITH_WARNINGS, EMPTY_OR_NON_TRADING, FAILED, and existing-file skips.

## Install into your GitHub repository

1. Create a new public or private GitHub repository, e.g. `nepse-floor-sheet-csv`.
2. Extract this ZIP.
3. Upload all extracted files/folders to the repository root, preserving `.github/workflows/floorsheet.yml`.
4. Open **Settings → Actions → General → Workflow permissions** and choose **Read and write permissions**.
5. Open **Actions → NEPSE Floor Sheet CSV** and enable workflows if prompted.
6. Run **Run workflow** with `from_date` and `to_date` set to `2026-10-15` and `2026-10-16` to test historical retrieval.
7. Inspect the workflow log and `data/floorsheet/status.csv`. A successful workflow run does not automatically mean every row has all broker names/time; check `SUCCESS_WITH_WARNINGS`.
8. Once the historical test works, the schedule runs Sunday–Thursday at 15:30 Nepal time. GitHub scheduled runs can be delayed, and schedules on the default branch are required.

## Backfill / retry

Use **Actions → NEPSE Floor Sheet CSV → Run workflow**:
- `from_date`: first date, YYYY-MM-DD
- `to_date`: last date, YYYY-MM-DD
- `retry_existing`: true to re-fetch dates that previously had EMPTY/FAILED status. Existing CSV files are preserved unless you delete them manually.

For large date ranges, process shorter ranges (e.g. one week at a time) and inspect `status.csv` after each run.

## Data completeness notes

- The script checks exact column order, non-empty data, `contractId`, `stockSymbol`, requested date, and row count.
- If the official API omits broker names, the output keeps the required columns but leaves those cells blank and flags the file.
- It deduplicates on `contractId`. If a future NEPSE response uses a different unique ID pattern, review the script before production use.
- A date with no rows may be a holiday/non-trading day, but it may also be an API issue. The status deliberately does not assume which one.
- Store very large archives with care: committing every daily CSV permanently increases Git repository size.

## References

- Official NEPSE floor-sheet page: https://nepalstock.com/floor-sheet
- Client source: https://github.com/basic-bgnr/NepseUnofficialApi
- Client method implementation: `nepse/NepseLib.py` (`getFloorSheet`, `getFloorSheetOf`)

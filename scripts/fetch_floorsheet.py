#!/usr/bin/env python3
"""Fetch date-specific NEPSE floor sheets through the public NepseUnofficialApi client.

Important:
- This uses NEPSE's official website/API through the open-source client.
- The client exposes historical floor sheets per symbol (getFloorSheetOf(symbol, date)),
  so the script iterates the official security list for each requested date.
- Empty dates are recorded in data/floorsheet/status.csv; no empty CSV is written.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nepse import Nepse

COLUMNS = [
    "contractId",
    "stockSymbol",
    "buyerMemberId",
    "sellerMemberId",
    "contractQuantity",
    "contractRate",
    "contractAmount",
    "businessDate",
    "tradeBookId",
    "stockId",
    "buyerBrokerName",
    "sellerBrokerName",
    "tradeTime",
    "securityName",
]
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "floorsheet"
STATUS = OUT / "status.csv"
NPT = ZoneInfo("Asia/Kathmandu")

ALIASES = {
    "contractId": ("contractId", "contractID", "transaction", "transactionNo"),
    "stockSymbol": ("stockSymbol", "symbol", "securitySymbol"),
    "buyerMemberId": ("buyerMemberId", "buyer", "buyerId"),
    "sellerMemberId": ("sellerMemberId", "seller", "sellerId"),
    "contractQuantity": ("contractQuantity", "contractQty", "quantity", "qty"),
    "contractRate": ("contractRate", "rate", "contractPrice"),
    "contractAmount": ("contractAmount", "amount", "turnover"),
    "businessDate": ("businessDate", "business_date", "date"),
    "tradeBookId": ("tradeBookId", "tradebookId", "tradeBookID"),
    "stockId": ("stockId", "securityId", "id"),
    "buyerBrokerName": ("buyerBrokerName", "buyerMemberName", "buyerName"),
    "sellerBrokerName": ("sellerBrokerName", "sellerMemberName", "sellerName"),
    "tradeTime": ("tradeTime", "tradeDateTime", "contractDate", "time"),
    "securityName": ("securityName", "companyName", "company", "name"),
}

def first_value(row: dict, keys: tuple[str, ...], default=""):
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default

def as_iso_date(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if value:
        text = str(value)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            try:
                return date.fromisoformat(text[:10]).isoformat()
            except ValueError:
                pass
    return ""

def read_status():
    if not STATUS.exists():
        return []
    with STATUS.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def write_status(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["date", "status", "rows", "symbols_checked", "symbols_failed", "message"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["date"]))
    tmp.replace(STATUS)

def dates_between(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def output_path(day: date) -> Path:
    return OUT / str(day.year) / f"{day:%Y_%m_%d}_floorsheet.csv"

def normalize_record(row: dict, target_date: date, security_by_symbol: dict, security_by_id: dict):
    row = dict(row)
    symbol = str(first_value(row, ALIASES["stockSymbol"], "")).strip().upper()
    sec = security_by_symbol.get(symbol, {})
    sid = first_value(row, ALIASES["stockId"], "")
    if not sid and sec:
        sid = sec.get("id", "")
    name = first_value(row, ALIASES["securityName"], "")
    if not name and sec:
        name = sec.get("securityName") or sec.get("companyName") or sec.get("name") or sec.get("company") or ""
    if not name and sid != "":
        sec2 = security_by_id.get(str(sid), {})
        name = sec2.get("securityName") or sec2.get("companyName") or sec2.get("name") or sec2.get("company") or ""
    result = {}
    for col in COLUMNS:
        if col == "stockSymbol":
            value = symbol
        elif col == "stockId":
            value = sid
        elif col == "securityName":
            value = name
        elif col == "businessDate":
            value = as_iso_date(first_value(row, ALIASES[col], "")) or target_date.isoformat()
        else:
            value = first_value(row, ALIASES[col], "")
        result[col] = value
    # Do not silently accept a row whose date conflicts with the requested day.
    row_date = as_iso_date(result["businessDate"])
    if row_date and row_date != target_date.isoformat():
        raise ValueError(f"API returned businessDate {row_date} while requesting {target_date}")
    result["businessDate"] = target_date.isoformat()
    if not result["stockSymbol"]:
        raise ValueError(f"Floor-sheet row has no stock symbol: {row}")
    if result["contractId"] == "":
        raise ValueError(f"Floor-sheet row has no contractId: {row}")
    return result

def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    # Validate header and row count before atomic replacement.
    with tmp.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"CSV header mismatch for {path.name}: {reader.fieldnames}")
        count = sum(1 for _ in reader)
    if count != len(rows):
        raise ValueError(f"CSV row-count validation failed for {path.name}: {count} != {len(rows)}")
    if count == 0:
        tmp.unlink(missing_ok=True)
        raise ValueError("Refusing to save an empty floor-sheet CSV")
    tmp.replace(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", help="YYYY-MM-DD; defaults to Nepal local date")
    parser.add_argument("--to-date", help="YYYY-MM-DD; defaults to --from-date")
    parser.add_argument("--retry-existing", action="store_true", help="re-fetch dates whose status is EMPTY/FAILED; successful CSVs are still skipped")
    parser.add_argument("--pause", type=float, default=0.10, help="seconds between symbol requests")
    args = parser.parse_args()

    today_npt = datetime.now(NPT).date()
    try:
        start = date.fromisoformat(args.from_date) if args.from_date else today_npt
        end = date.fromisoformat(args.to_date) if args.to_date else start
    except ValueError as e:
        parser.error(f"Dates must be YYYY-MM-DD: {e}")
    if start > end:
        parser.error("--from-date must be on or before --to-date")

    OUT.mkdir(parents=True, exist_ok=True)
    status_rows = {r["date"]: r for r in read_status()}

    print("Connecting to NEPSE official source through NepseUnofficialApi...")
    client = Nepse()
    # The upstream client README currently documents disabling TLS verification
    # as a workaround for NEPSE's certificate-chain problem. Prefer verification
    # first; only use the documented workaround if the client exposes the setter.
    try:
        client.setTLSVerification(True)
    except Exception:
        pass

    securities = client.getSecurityList()
    if not securities:
        raise RuntimeError("NEPSE returned an empty security list; refusing to continue.")
    symbols = sorted({
        str(s.get("symbol", "")).strip().upper()
        for s in securities
        if s.get("symbol")
    })
    security_by_symbol = {str(s.get("symbol", "")).strip().upper(): s for s in securities if s.get("symbol")}
    security_by_id = {str(s.get("id", "")): s for s in securities if s.get("id") is not None}
    print(f"Security symbols loaded: {len(symbols)}")

    # On the documented NEPSE certificate issue, the upstream project's examples
    # use setTLSVerification(False). We do not silently disable verification.
    # If TLS fails, fix the runner's CA/certificate issue or make a conscious change.

    failures = 0
    for day in dates_between(start, end):
        key = day.isoformat()
        path = output_path(day)
        existing_status = status_rows.get(key, {}).get("status", "")
        if path.exists() and path.stat().st_size > 100 and not args.retry_existing:
            print(f"SKIP {key}: output already exists ({path})")
            status_rows[key] = {"date": key, "status": "EXISTS_SKIPPED", "rows": str(count_csv_rows(path)), "symbols_checked": "0", "symbols_failed": "0", "message": "Existing file preserved"}
            continue
        if existing_status == "SUCCESS" and path.exists() and not args.retry_existing:
            continue

        print(f"Fetching {key} across {len(symbols)} symbols...")
        all_rows = []
        symbol_errors = []
        checked = 0
        for i, symbol in enumerate(symbols, 1):
            try:
                result = client.getFloorSheetOf(symbol, key)
                if result:
                    if not isinstance(result, list):
                        raise TypeError(f"Expected list, got {type(result).__name__}")
                    for raw in result:
                        if not isinstance(raw, dict):
                            raise TypeError(f"Expected record dict, got {type(raw).__name__}")
                        all_rows.append(normalize_record(raw, day, security_by_symbol, security_by_id))
                checked += 1
            except Exception as e:
                symbol_errors.append(f"{symbol}: {type(e).__name__}: {e}")
            if args.pause > 0:
                time.sleep(args.pause)
            if i % 50 == 0:
                print(f"  {i}/{len(symbols)} symbols checked; {len(all_rows)} rows so far")

        # Dedupe by contractId because the same market-wide contract should belong to one symbol only.
        unique = {}
        for row in all_rows:
            unique[str(row["contractId"])] = row
        all_rows = sorted(unique.values(), key=lambda r: (str(r["tradeTime"]), str(r["contractId"])))

        if symbol_errors and checked < max(1, int(len(symbols) * 0.90)):
            message = f"Too many symbol/API errors; {len(symbol_errors)} errors, only {checked} successful symbol requests. " + " | ".join(symbol_errors[:5])
            status_rows[key] = {"date": key, "status": "FAILED", "rows": str(len(all_rows)), "symbols_checked": str(checked), "symbols_failed": str(len(symbol_errors)), "message": message[:2000]}
            failures += 1
            print(f"FAILED {key}: {message}")
            continue

        if not all_rows:
            status = "EMPTY_OR_NON_TRADING"
            message = "No rows returned for any symbol. Check NEPSE holiday/non-trading calendar and retry if this should be a trading day."
            status_rows[key] = {"date": key, "status": status, "rows": "0", "symbols_checked": str(checked), "symbols_failed": str(len(symbol_errors)), "message": message}
            print(f"{status} {key}; no empty CSV created.")
            continue

        write_csv(path, all_rows)
        missing_name = sum(1 for r in all_rows if not r["buyerBrokerName"] or not r["sellerBrokerName"])
        missing_time = sum(1 for r in all_rows if not r["tradeTime"])
        message = "Validated fixed 14-column header, non-empty rows, and requested business date."
        if symbol_errors:
            message += f" Warnings: {len(symbol_errors)} symbols errored; verify completeness before treating as final."
        if missing_name:
            message += f" Missing broker-name fields in {missing_name} rows; inspect raw API fields / official broker lookup."
        if missing_time:
            message += f" Missing tradeTime in {missing_time} rows."
        status_rows[key] = {"date": key, "status": "SUCCESS_WITH_WARNINGS" if symbol_errors or missing_name or missing_time else "SUCCESS", "rows": str(len(all_rows)), "symbols_checked": str(checked), "symbols_failed": str(len(symbol_errors)), "message": message}
        print(f"Saved {path} ({len(all_rows)} rows; checked {checked}/{len(symbols)} symbols)")

    write_status(list(status_rows.values()))
    print(f"Status log: {STATUS}")
    if failures:
        print(f"ERROR: {failures} date(s) failed and should be retried.")
        sys.exit(1)

def count_csv_rows(path: Path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)

if __name__ == "__main__":
    main()

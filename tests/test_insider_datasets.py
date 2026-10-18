import io
import zipfile

from src.adapters.sec_insider_datasets import parse_purchases


def _zip(files: dict[str, list[list[str]]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, rows in files.items():
            z.writestr(f"2025q1_form345/{name}", "\n".join("\t".join(r) for r in rows) + "\n")
    return buf.getvalue()


def test_keeps_open_market_buys_of_tracked_issuers_summed_per_filing():
    body = _zip({
        "SUBMISSION.tsv": [["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "AFF10B5ONE"],
                           ["a1", "05-MAR-2025", "4", "320193", "0"],
                           ["a2", "06-MAR-2025", "4", "320193", "1"],
                           ["a3", "07-MAR-2025", "4", "999", ""],      # issuer not tracked
                           ["a4", "08-MAR-2025", "3", "320193", ""]],  # Form 3
        "NONDERIV_TRANS.tsv": [["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                "TRANS_PRICEPERSHARE"],
                               ["a1", "03-MAR-2025", "P", "100", "10.5"],
                               ["a1", "04-MAR-2025", "P", "50", "11"],
                               ["a1", "04-MAR-2025", "S", "500", "11"],
                               ["a2", "04-MAR-2025", "P", "10", "10"],
                               ["a3", "04-MAR-2025", "P", "10", "10"]],
        "REPORTINGOWNER.tsv": [["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
                               ["a1", "Doe Jane", "Director", ""],
                               ["a1", "Fund LP", "TenPercentOwner", ""],
                               ["a2", "Roe Rich", "Officer", "CFO"]],
    })
    rows = {(r["accession"], r["ticker"]): r for r in parse_purchases(body, {320193: ["AAPL", "AAPL-B"]})}
    assert set(rows) == {("a1", "AAPL"), ("a1", "AAPL-B"), ("a2", "AAPL"), ("a2", "AAPL-B")}
    a1 = rows[("a1", "AAPL")]
    assert a1["buy_value"] == 1600.0 and a1["filed_at"] == "2025-03-05"
    assert a1["first_trade_date"] == "2025-03-03" and a1["owner"] == "Doe Jane" and a1["role"] == "director"
    assert a1["is_10b5_1"] is False and rows[("a2", "AAPL")]["is_10b5_1"] is True
    assert rows[("a2", "AAPL")]["role"] == "CFO"

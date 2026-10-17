from src.adapters.sec_edgar import (
    html_to_text,
    parse_form4,
    parse_index_url,
    parse_submissions,
    pick_text_document,
)

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <aff10b5One>1</aff10b5One>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-10-14</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>50.5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>400</value></transactionShares>
        <transactionPricePerShare><value></value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_index_url():
    url = "https://www.sec.gov/Archives/edgar/data/1506293/000150629326000143/0001506293-26-000143-index.html"
    assert parse_index_url(url) == (1506293, "000150629326000143")
    assert parse_index_url(None) is None


def test_submissions_items_by_accession():
    doc = {"filings": {"recent": {"accessionNumber": ["0001506293-26-000143", "0001506293-26-000150"],
                                  "items": ["2.02,9.01", ""],
                                  "primaryDocument": ["pins-8k.htm", "xslF345X05/form4.xml"]}}}
    out = parse_submissions(doc)
    assert out["000150629326000143"] == {"items": ["2.02", "9.01"], "primary": "pins-8k.htm"}
    assert out["000150629326000150"]["items"] == []


def test_press_release_preferred():
    idx = {"directory": {"item": [{"name": "pins-8k.htm"}, {"name": "pins-ex991.htm"}, {"name": "R1.xml"}]}}
    assert pick_text_document(idx, "pins-8k.htm") == "pins-ex991.htm"
    assert pick_text_document({"directory": {"item": [{"name": "a.htm"}]}}, "a.htm") == "a.htm"


def test_html_to_text():
    assert html_to_text("<p>Revenue&nbsp;rose <b>12%</b></p><script>x()</script>") == "Revenue rose 12%"


def test_form4_summary():
    s = parse_form4(FORM4).summary()
    assert s["owner"] == "Doe Jane" and s["role"] == "Chief Financial Officer"
    assert s["is_10b5_1"] is True
    assert s["codes"] == ["M", "S"]
    assert s["sell_value"] == 50500.0 and s["buy_value"] == 0
    assert s["net_shares"] == -600.0


def test_parse_company_tickers():
    from src.adapters.sec_edgar import parse_company_tickers
    doc = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"},
           "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire"}}
    assert parse_company_tickers(doc) == {"AAPL": 320193, "BRK-B": 1067983}


def test_parse_recent_filings_filters_date_and_form():
    from src.adapters.sec_edgar import parse_recent_filings
    doc = {"filings": {"recent": {
        "accessionNumber": ["0000316709-26-000035", "0000316709-26-000030", "0000316709-26-000020"],
        "form": ["8-K", "S-8", "4"],
        "filingDate": ["2026-10-15", "2026-10-14", "2026-09-01"],
        "acceptanceDateTime": ["2026-10-15T07:30:12.000Z", "", ""],
        "items": ["2.02,9.01", "", ""],
        "primaryDocument": ["schw-20261015.htm", "", ""],
        "reportDate": ["2026-10-15", "", ""],
    }}}
    out = parse_recent_filings(doc, "2026-10-01", {"8-K", "4"})
    assert [f["accession"] for f in out] == ["0000316709-26-000035"]
    assert out[0]["items"] == ["2.02", "9.01"] and out[0]["accepted"].startswith("2026-10-15")


def test_to_raw_filing_builds_index_url_parse_index_url_reads():
    from src.adapters.sec_edgar import parse_index_url
    from src.jobs.ingest_filings import resolve_ciks, to_raw_filing
    f = {"accession": "0000316709-26-000035", "form": "8-K", "filing_date": "2026-10-15",
         "accepted": None, "items": ["2.02"], "primary": "x.htm", "report_date": None}
    r = to_raw_filing("SCHW", 316709, f)
    assert parse_index_url(r.url) == (316709, "000031670926000035")
    assert r.filed_at.isoformat() == "2026-10-15T00:00:00+00:00" and r.source == "sec_edgar"
    assert resolve_ciks(["BRK.B", "ZZZ", "OLD"], {"BRK-B": 1}, {"OLD": 7}) == {"BRK.B": 1, "OLD": 7}


def test_html_to_text_drops_sgml_header():
    from src.adapters.sec_edgar import html_to_text
    raw = "EX-99.1 2 a3q26exhibit991.htm EX-99.1 Document <p>Exhibit 99.1</p><p>SCHWAB DELIVERS</p>"
    assert html_to_text(raw) == "Exhibit 99.1 SCHWAB DELIVERS"
    assert html_to_text("<p>Results for 2026</p>") == "Results for 2026"

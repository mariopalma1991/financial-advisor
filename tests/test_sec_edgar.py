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

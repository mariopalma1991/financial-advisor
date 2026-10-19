from src.adapters.sec_risk_factors import compare, html_to_paragraphs, item_1a

LONG = "We may be unable to keep our largest customers, and losing any of them would reduce our revenue materially"


def _doc(*risks: str) -> str:
    toc = "<p>Item 1A. Risk Factors</p><p>12</p><p>Item 1B. Unresolved Staff Comments</p>"
    body = "".join(f"<p>{r}</p>" for r in risks)
    return (f"<html><body>{toc}<p>Item 1. Business</p><p>{LONG} in business.</p>"
            f"<div><b>ITEM 1A.&nbsp;RISK FACTORS</b></div>{body}<p>Item 1B. Unresolved Staff Comments</p>"
            f"<p>Item 2. Properties</p><p>{LONG} in properties.</p></body></html>")


def test_item_1a_skips_the_table_of_contents_and_short_lines():
    paras = item_1a(html_to_paragraphs(_doc(f"{LONG} (2025).", "Short heading", f"{LONG} in Mexico.")))
    assert paras == [f"{LONG} (2025).", f"{LONG} in Mexico."]


def test_compare_ignores_changed_years_and_flags_new_risks():
    prior = [f"{LONG} (2024).", f"{LONG} in Mexico."]
    tariffs = "New tariffs on imported parts could raise our costs, cut margins and force price increases"
    cur = [f"{LONG} (2025).", f"{LONG} in Mexico, where new rules on foreign investment apply.", tariffs]
    c = compare(cur, prior)
    assert c["added"] == [tariffs] and c["n_removed"] == 0 and c["n_reworded"] == 1
    assert 0.8 < c["similarity"] < 1.0
    assert compare(prior, prior)["similarity"] == 1.0

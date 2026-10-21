from src.adapters.sec_edgar import parse_sic
from src.transforms.peers import MAX_PEERS, pick_peers


def test_same_sic_code_beats_finnhubs_broad_bucket():
    # OSCR is a health insurer (6324); life insurers (6311) share only the 3-digit group
    u = {t: (6324, cap) for t, cap in [("OSCR", 5e3), ("UNH", 4e5), ("CNC", 2e4), ("ELV", 8e4), ("MOH", 1e4)]}
    u |= {t: (6311, 4e4) for t in ("MET", "PRU", "AFL")}
    peers, basis = pick_peers(u)["OSCR"]
    assert basis == "sic4"
    assert peers == ["MOH", "CNC", "ELV", "UNH"]  # closest market cap first


def test_falls_back_to_three_digits_then_to_nothing():
    u = {"A": (6311, 1.0), "B": (6311, 1.0), "C": (6312, 1.0), "D": (6319, 1.0), "E": (6310, 1.0),
         "X": (1311, 1.0), "Y": (1311, 1.0), "Z": (None, 1.0)}
    out = pick_peers(u)
    assert out["A"] == (["B", "C", "D", "E"], "sic3")
    assert "X" not in out and "Z" not in out


def test_caps_the_group_and_puts_missing_market_caps_last():
    u = {f"T{i:02d}": (7372, float(i + 1)) for i in range(20)} | {"NOCAP": (7372, None)}
    peers, _ = pick_peers(u)["T00"]
    assert len(peers) == MAX_PEERS and peers[0] == "T01" and "NOCAP" not in peers
    assert pick_peers(u)["NOCAP"][0][0] == "T00"  # no size: alphabetical


def test_parse_sic():
    assert parse_sic({"sic": "6324", "sicDescription": "Hospital & Medical Service Plans"}) == \
        (6324, "Hospital & Medical Service Plans")
    assert parse_sic({"sic": "", "sicDescription": ""}) == (None, None)

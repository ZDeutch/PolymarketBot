"""
Fetches current FIDE ratings for known chess players from the official
FIDE monthly XML rating lists, and shows a diff against KNOWN_RATINGS.

FIDE publishes updated lists on the 1st of each month. This replaces
the manual "copy from 2700chess.com" workflow for KNOWN_RATINGS.

Usage:
    venv/bin/python -m fetchers.fide_fetcher           # print all ratings
    venv/bin/python -m fetchers.fide_fetcher --diff    # compare vs KNOWN_RATINGS
    venv/bin/python -m fetchers.fide_fetcher --fmt rapid --diff

Note: Downloads are ~5–15 MB each; allow 10–30 seconds per format.
If URLs break, check: https://ratings.fide.com/download_lists.phtml
"""

import io
import sys
import zipfile
import xml.etree.ElementTree as ET

import requests

# ─── FIDE download URLs ───────────────────────────────────────────────────────
# Updated monthly on the 1st. Separate lists per time control.
FIDE_URLS = {
    "classical": "https://ratings.fide.com/download/standard_rating_list_xml.zip",
    "rapid":     "https://ratings.fide.com/download/rapid_rating_list_xml.zip",
    "blitz":     "https://ratings.fide.com/download/blitz_rating_list_xml.zip",
}

HEADERS = {"User-Agent": "PolymarketBot/1.0 fide-rating-fetcher"}


# ─── Player → FIDE ID mapping ────────────────────────────────────────────────
# Verify any ID at: https://ratings.fide.com/profile/{FIDE_ID}
# Only the canonical name is listed here (no PGN-variant aliases needed).
FIDE_PLAYER_IDS: dict[str, str] = {
    # Top 10 / Candidates 2026
    # All IDs verified against the FIDE XML standard_rating_list.xml
    "Magnus Carlsen":             "1503014",
    "Fabiano Caruana":            "2020009",
    "Hikaru Nakamura":            "2016192",
    "Alireza Firouzja":           "12573981",
    "Javokhir Sindarov":          "14205483",
    "Nodirbek Abdusattorov":      "14204118",
    "Vincent Keymer":             "12940690",
    "Arjun Erigaisi":             "35009192",   # XML: "Erigaisi Arjun"
    "Wesley So":                  "5202213",
    "Wei Yi":                     "8603405",    # XML: "Wei, Yi"
    "Anish Giri":                 "24116068",
    "Ian Nepomniachtchi":         "4168119",
    "Gukesh D":                   "46616543",
    "Praggnanandhaa R":           "25059530",   # XML: "Praggnanandhaa R"
    "Andrey Esipenko":            "24175439",
    "Ding Liren":                 "8603677",
    # GCT / Norway Chess 2026
    "Maxime Vachier-Lagrave":     "623539",
    "Jan-Krzysztof Duda":         "1170546",
    "Levon Aronian":              "13300474",
    "Bogdan-Daniel Deac":         "1226380",
    "Ivan Saric":                 "14508150",
    "Radoslaw Wojtaszek":         "1118358",
    # Sigeman 2026
    "Nils Grandelius":            "1710400",
    "Yagiz Kaan Erdogmus":        "44599790",
    "Andy Woodward":              "30953499",
    "Zhu Jiner":                  "8608059",
    # Other elite players in KNOWN_RATINGS
    "Matthias Blübaum":           "24651516",   # XML: "Bluebaum, Matthias"
    "Aravindh Chithambaram":      "5072786",    # XML: "Aravindh, Chithambaram VR."
    "Hans Moke Niemann":          "2093596",
    "Vladimir Fedoseev":          "24130737",
    "Thai Dai Van Nguyen":        "358878",
    "Max Warmerdam":              "1048104",
    "Jorden van Foreest":         "1039784",
    "Nijat Abasov":               "13402960",
    "Vidit Gujrathi":             "5029465",    # XML: "Vidit, Santosh Gujrathi"
    "Alexei Shirov":              "2209390",
}

# Invert: fide_id → canonical_name  (built once at import)
_ID_TO_NAME: dict[str, str] = {v: k for k, v in FIDE_PLAYER_IDS.items()}


# ─── XML parsing ─────────────────────────────────────────────────────────────

def _iterparse_ratings(xml_file, fide_ids: set) -> dict[str, int]:
    """Stream-parses a FIDE XML rating list and returns {fide_id: rating}.

    Uses iterparse so the full (potentially 100 MB+) XML is never fully
    in memory. Clears each player element after processing.

    The <rating> tag in each list is the rating for that list's format
    (standard for the standard list, rapid for rapid list, etc.).
    """
    results: dict[str, int] = {}
    current_id: str | None = None

    for _event, elem in ET.iterparse(xml_file, events=("end",)):
        tag = elem.tag

        if tag == "fideid":
            current_id = (elem.text or "").strip()

        elif tag == "rating":
            if current_id in fide_ids:
                try:
                    results[current_id] = int(elem.text or "")
                except (TypeError, ValueError):
                    pass

        elif tag == "player":
            current_id = None
            elem.clear()   # free memory

    return results


def _fetch_format(fmt: str) -> dict[str, int]:
    """Downloads the FIDE rating list for the given format.

    Returns {player_name: rating} for all players in FIDE_PLAYER_IDS.
    """
    url = FIDE_URLS.get(fmt)
    if not url:
        raise ValueError(f"Unknown format '{fmt}'. Choose: {list(FIDE_URLS)}")

    fide_ids = set(FIDE_PLAYER_IDS.values())

    print(f"  Downloading FIDE {fmt} list ...", end="", flush=True)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=120)
        resp.raise_for_status()
    except Exception as e:
        print(f" FAILED: {e}")
        return {}
    print(f" {len(resp.content) // 1024} KB")

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = next(
                (n for n in zf.namelist() if n.lower().endswith(".xml")), None
            )
            if not xml_name:
                print("  ERROR: no XML file found in zip")
                return {}
            print(f"  Parsing {xml_name} ...", end="", flush=True)
            with zf.open(xml_name) as xml_file:
                id_ratings = _iterparse_ratings(xml_file, fide_ids)
    except Exception as e:
        print(f" ERROR: {e}")
        return {}

    # Convert fide_id → player_name
    name_ratings: dict[str, int] = {}
    for fide_id, rating in id_ratings.items():
        name = _ID_TO_NAME.get(fide_id)
        if name:
            name_ratings[name] = rating

    found = len(name_ratings)
    total = len(FIDE_PLAYER_IDS)
    print(f" found {found}/{total} players")
    return name_ratings


# ─── Public API ───────────────────────────────────────────────────────────────

def fetch_ratings(fmt: str = "classical") -> dict[str, int]:
    """Fetch current FIDE ratings for all tracked players.

    fmt: 'classical', 'rapid', or 'blitz'
    Returns {player_name: rating}
    """
    return _fetch_format(fmt)


def fetch_all_ratings() -> dict[str, dict[str, int]]:
    """Fetch classical, rapid, and blitz ratings for all tracked players.

    Returns {player_name: {'classical': int, 'rapid': int, 'blitz': int}}
    Missing values are omitted (not all players have rapid/blitz lists).
    """
    classical = _fetch_format("classical")
    rapid     = _fetch_format("rapid")
    blitz     = _fetch_format("blitz")

    all_names = set(classical) | set(rapid) | set(blitz)
    result: dict[str, dict[str, int]] = {}
    for name in sorted(all_names):
        entry = {}
        if name in classical:
            entry["classical"] = classical[name]
        if name in rapid:
            entry["rapid"] = rapid[name]
        if name in blitz:
            entry["blitz"] = blitz[name]
        result[name] = entry
    return result


# ─── Diff helper ──────────────────────────────────────────────────────────────

def _load_known_ratings(fmt: str) -> dict[str, int]:
    """Imports and returns the appropriate KNOWN_RATINGS* dict from chess_fetcher."""
    from fetchers.chess_fetcher import (
        KNOWN_RATINGS, KNOWN_RATINGS_RAPID, KNOWN_RATINGS_BLITZ,
    )
    mapping = {
        "classical": KNOWN_RATINGS,
        "rapid":     KNOWN_RATINGS_RAPID,
        "blitz":     KNOWN_RATINGS_BLITZ,
    }
    return mapping.get(fmt, KNOWN_RATINGS)


def print_diff(fmt: str = "classical") -> None:
    """Prints a diff between live FIDE ratings and KNOWN_RATINGS in chess_fetcher.py.

    Shows players where the live rating differs from the hardcoded value,
    so you know exactly which lines to update.
    """
    print(f"\n[fide_fetcher] Fetching {fmt} ratings from FIDE...")
    live    = fetch_ratings(fmt)
    known   = _load_known_ratings(fmt)

    # Only compare canonical names (not PGN-variant aliases in KNOWN_RATINGS)
    canonical_names = set(FIDE_PLAYER_IDS.keys())
    changes  = []
    no_data  = []
    same     = []

    for name in sorted(canonical_names):
        live_r  = live.get(name)
        known_r = known.get(name)

        if live_r is None:
            no_data.append(name)
        elif known_r is None:
            changes.append((name, None, live_r, "NEW"))
        elif live_r != known_r:
            diff = live_r - known_r
            sign = "+" if diff > 0 else ""
            changes.append((name, known_r, live_r, f"{sign}{diff}"))
        else:
            same.append(name)

    print(f"\n{'─'*60}")
    print(f"  FIDE {fmt.upper()} — diff vs KNOWN_RATINGS")
    print(f"{'─'*60}")

    if changes:
        print(f"\n  CHANGED ({len(changes)} players):")
        for name, old, new, delta in changes:
            old_str = str(old) if old is not None else "(not in KNOWN_RATINGS)"
            print(f"    {name:<35} {old_str:>6} → {new:<6}  ({delta})")
        print()
        print("  Copy-paste updates:")
        for name, old, new, delta in changes:
            # Use the longest alias (canonical name) as the key
            print(f'    "{name}": {new},')
    else:
        print("\n  No changes — KNOWN_RATINGS is up to date.")

    if no_data:
        print(f"\n  NO FIDE DATA for ({len(no_data)} players):")
        for name in no_data:
            fid = FIDE_PLAYER_IDS.get(name, "?")
            print(f"    {name:<35}  FIDE ID {fid} — verify at "
                  f"https://ratings.fide.com/profile/{fid}")

    print(f"\n  Unchanged: {len(same)} players")
    print(f"{'─'*60}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Fetch FIDE ratings and compare with KNOWN_RATINGS."
    )
    ap.add_argument(
        "--fmt",
        choices=["classical", "rapid", "blitz", "all"],
        default="classical",
        help="Rating format (default: classical)",
    )
    ap.add_argument(
        "--diff",
        action="store_true",
        help="Show diff vs KNOWN_RATINGS in chess_fetcher.py",
    )
    args = ap.parse_args()

    if args.diff:
        formats = ["classical", "rapid", "blitz"] if args.fmt == "all" else [args.fmt]
        for fmt in formats:
            print_diff(fmt)
    else:
        if args.fmt == "all":
            print("[fide_fetcher] Fetching all formats...")
            all_r = fetch_all_ratings()
            print(f"\n{'─'*60}")
            print(f"  {'Player':<35} {'Classical':>9} {'Rapid':>6} {'Blitz':>6}")
            print(f"{'─'*60}")
            for name, r in all_r.items():
                c = str(r.get("classical", "–")).rjust(9)
                ra = str(r.get("rapid",     "–")).rjust(6)
                bl = str(r.get("blitz",     "–")).rjust(6)
                print(f"  {name:<35} {c} {ra} {bl}")
            print(f"{'─'*60}\n")
        else:
            print(f"[fide_fetcher] Fetching {args.fmt} ratings...")
            ratings = fetch_ratings(args.fmt)
            print(f"\n{'─'*50}")
            print(f"  {'Player':<35} {'Rating':>6}")
            print(f"{'─'*50}")
            for name, r in sorted(ratings.items(), key=lambda x: -x[1]):
                print(f"  {name:<35} {r:>6}")
            print(f"{'─'*50}")
            print(f"  Total: {len(ratings)} players\n")

"""
retry_failed.py
Retry the 31 failed artists using URL normalisation variants.
Updates song-list-export-updated.json, verified_log.csv, unmatched_artists.csv in place.
"""

import json, csv, re, time, urllib.parse
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from pathlib import Path

BASE = Path(__file__).parent
JSON_PATH    = BASE / "song-list-export-updated.json"
LOG_PATH     = BASE / "verified_log.csv"
UNMATCHED    = BASE / "unmatched_artists.csv"

FUZZY_THRESHOLD = 80
REQUEST_DELAY   = 1.5
TIMEOUT         = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

FLAT_TO_SHARP = {"Bb":"A#","Db":"C#","Eb":"D#","Gb":"F#","Ab":"G#"}
NOTE_RE = re.compile(r"^([A-Ga-g][b#]?\d)\s*[-–]\s*([A-Ga-g][b#]?\d)$")


def normalise_note(note):
    note = note.strip()
    for flat, sharp in FLAT_TO_SHARP.items():
        if note.startswith(flat):
            return sharp + note[len(flat):]
    return note


def normalise_title(t):
    return re.sub(r"[^\w\s]", "", t.lower()).strip()


def url_variants(artist):
    """Generate candidate URL names to try for a given artist string."""
    variants = []

    # Strip parenthetical suffixes: "(male key)", "(Javert)", "(cover)", etc.
    stripped = re.sub(r"\s*\([^)]+\)\s*$", "", artist).strip()
    if stripped != artist:
        variants.append(stripped)

    # The base to permute from
    base = stripped if stripped else artist

    variants.append(base)

    # Add "The " prefix
    if not base.lower().startswith("the "):
        variants.append("The " + base)

    # Remove "The " prefix
    if base.lower().startswith("the "):
        variants.append(base[4:])

    # & variants — site is particular about capitalisation
    if " & " in base:
        # lowercase 'the' after &
        variants.append(re.sub(r" & The ", " & the ", base))
        variants.append(re.sub(r" & the ", " & The ", base))
        variants.append(base.replace(" & ", " and "))
        variants.append(base.replace(" & ", " & "))  # same, just ensures it's there
        # strip everything after " & " — just primary artist
        primary = base.split(" & ")[0].strip()
        variants.append(primary)

    # Handle "X, Y & Z" style (CSN) — try stripping commas
    if "," in base and " & " in base:
        no_comma = base.replace(",", "")
        variants.append(no_comma)
        variants.append(no_comma.replace(" & ", " and "))

    # Handle "X / Y" — try just first part
    if " / " in base:
        variants.append(base.split(" / ")[0].strip())

    # Handle "X, Y and Z" with comma — strip commas
    if "," in base:
        variants.append(base.replace(",", ""))

    # Deduplicate preserving order
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def fetch_and_parse(artist_query, session):
    """Try fetching artist_query. Returns (list_of_songs, used_url) or (None, url)."""
    url = "https://singingcarrots.com/artist-range?artist=" + urllib.parse.quote(artist_query)
    for attempt in range(2):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                songs = parse_songs(r.text, artist_query)
                return songs, url
            if attempt == 0:
                time.sleep(2)
        except Exception:
            if attempt == 0:
                time.sleep(2)
    return None, url


def parse_songs(html, requested_artist):
    soup = BeautifulSoup(html, "html.parser")
    # Sanity-check first word appears on page
    first_word = requested_artist.split()[0].lower().rstrip(",")
    if first_word not in soup.get_text(" ", strip=True).lower():
        return None
    songs = []
    for link in soup.find_all("a", href=re.compile(r"/song\?song=")):
        title = link.get_text(strip=True)
        if not title:
            continue
        for sib in link.next_siblings:
            if hasattr(sib, "name") and sib.name == "span":
                m = NOTE_RE.match(sib.get_text(strip=True))
                if m:
                    songs.append({
                        "title": title,
                        "low":   normalise_note(m.group(1)),
                        "high":  normalise_note(m.group(2)),
                    })
                break
            if hasattr(sib, "strip") and str(sib).strip(" ()"):
                break
    return songs


def best_match(scraped_title, candidates):
    norm_s = normalise_title(scraped_title)
    best_score, best_cand = 0.0, None
    for c in candidates:
        score = fuzz.token_sort_ratio(norm_s, normalise_title(c))
        if score > best_score:
            best_score, best_cand = score, c
    if best_score >= FUZZY_THRESHOLD:
        return best_cand, best_score
    return None, best_score


def main():
    # Load current state
    with open(JSON_PATH) as f:
        songs = json.load(f)

    # Load existing verified log
    existing_log = []
    with open(LOG_PATH) as f:
        existing_log = list(csv.DictReader(f))

    # Load unmatched list — only retry fetch_failed rows
    with open(UNMATCHED) as f:
        unmatched_rows = list(csv.DictReader(f))

    failed_rows = [r for r in unmatched_rows if r["reason"] == "fetch_failed"]
    other_rows  = [r for r in unmatched_rows if r["reason"] != "fetch_failed"]

    print(f"Retrying {len(failed_rows)} fetch_failed artists ({sum(len(r['song_titles_still_unverified'].split(';')) for r in failed_rows)} songs)")

    # Index songs by cleaned artist
    def clean_artist(raw):
        return re.sub(r"\s*\(male cover key\)\s*", "", raw, flags=re.IGNORECASE).strip()

    artist_to_songs = {}
    for s in songs:
        a = clean_artist(s["artist"])
        artist_to_songs.setdefault(a, []).append(s)

    session = requests.Session()
    new_verified_rows = []
    still_failed = []

    for i, row in enumerate(failed_rows, 1):
        original_artist = row["artist"]
        # Use the artist name as stored (may have been cleaned already)
        cleaned = clean_artist(original_artist)
        my_songs = artist_to_songs.get(cleaned, [])
        if not my_songs:
            still_failed.append({**row, "reason": "no_songs_in_dataset"})
            continue

        my_titles = [s["title"] for s in my_songs if not s.get("verified")]

        variants = url_variants(cleaned)
        print(f"[{i}/{len(failed_rows)}] {cleaned}")
        print(f"  Trying: {variants}")

        scraped = None
        used_url = None
        for variant in variants:
            time.sleep(REQUEST_DELAY)
            result, url = fetch_and_parse(variant, session)
            if result is not None and len(result) > 0:
                scraped = result
                used_url = url
                print(f"  ✓ '{variant}' → {len(result)} songs")
                break
            else:
                status = "no songs" if result is not None else "fetch failed"
                print(f"  ✗ '{variant}' → {status}")

        if not scraped:
            still_failed.append({**row, "reason": "fetch_failed_all_variants"})
            continue

        matched = set()
        for scraped_song in scraped:
            cand, score = best_match(scraped_song["title"], my_titles)
            if cand is None:
                continue
            for s in my_songs:
                if s["title"] == cand and cand not in matched and not s.get("verified"):
                    matched.add(cand)
                    old_low, old_high = s["low"], s["high"]
                    s["low"]      = scraped_song["low"]
                    s["high"]     = scraped_song["high"]
                    s["verified"] = True
                    new_verified_rows.append({
                        "title":      s["title"],
                        "artist":     cleaned,
                        "old_low":    old_low,
                        "old_high":   old_high,
                        "new_low":    scraped_song["low"],
                        "new_high":   scraped_song["high"],
                        "source_url": used_url,
                    })
                    break

        still_unmatched = [s["title"] for s in my_songs if s["title"] not in matched and not s.get("verified")]
        if still_unmatched:
            still_failed.append({
                "artist": cleaned,
                "song_titles_still_unverified": "; ".join(still_unmatched),
                "reason": f"no_confident_match after retry (scraped {len(scraped)} songs, matched {len(matched)}/{len(my_titles)})",
            })
        print(f"  matched {len(matched)}/{len(my_titles)}")

    # Write outputs
    with open(JSON_PATH, "w") as f:
        json.dump(songs, f, indent=2)

    all_log = existing_log + new_verified_rows
    with open(LOG_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title","artist","old_low","old_high","new_low","new_high","source_url"])
        w.writeheader()
        w.writerows(all_log)

    updated_unmatched = other_rows + still_failed
    with open(UNMATCHED, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["artist","song_titles_still_unverified","reason"])
        w.writeheader()
        w.writerows(updated_unmatched)

    # Summary
    newly = len(new_verified_rows)
    total_verified = sum(1 for s in songs if s.get("verified"))
    still_unverified = len(songs) - total_verified
    pct = total_verified / len(songs) * 100

    print("\n" + "="*60)
    print(f"Newly verified this run: {newly}")
    print(f"Total verified:          {total_verified} / {len(songs)}  ({pct:.1f}%)")
    print(f"Still unverified:        {still_unverified}")
    print(f"Still failed artists:    {len(still_failed)}")
    print("="*60)


if __name__ == "__main__":
    main()

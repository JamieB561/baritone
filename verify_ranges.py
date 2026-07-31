"""
verify_ranges.py
Verify baritone song vocal ranges against singingcarrots.com
"""

import json
import re
import csv
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
INPUT  = BASE / "song-list-export.json"
OUTPUT = BASE / "song-list-export-updated.json"
LOG    = BASE / "verified_log.csv"
UNMATCHED = BASE / "unmatched_artists.csv"

# ── Constants ─────────────────────────────────────────────────────────────────
SKIP_ARTISTS = {"Jazz Standard", "Traditional", "Traditional Gospel", "Traditional Praise"}
FUZZY_THRESHOLD = 80          # SequenceMatcher-style 0-100 score minimum
REQUEST_DELAY  = 1.5          # seconds between requests
TIMEOUT        = 15           # seconds per HTTP request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── Note normalisation ────────────────────────────────────────────────────────
FLAT_TO_SHARP = {
    "Bb": "A#", "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#",
    "bb": "A#", "db": "C#", "eb": "D#", "gb": "F#", "ab": "G#",
}

def normalise_note(note: str) -> str:
    """Convert flat notation to sharp, preserve octave digit."""
    note = note.strip()
    for flat, sharp in FLAT_TO_SHARP.items():
        if note.startswith(flat):
            return sharp + note[len(flat):]
    return note


def normalise_title(t: str) -> str:
    """Lowercase, strip punctuation for fuzzy matching."""
    return re.sub(r"[^\w\s]", "", t.lower()).strip()


# ── Scraping ──────────────────────────────────────────────────────────────────
def fetch_artist_page(artist: str, session: requests.Session):
    """Fetch artist-range page. Returns (html, url) or (None, url) on failure."""
    url = "https://singingcarrots.com/artist-range?artist=" + urllib.parse.quote(artist)
    for attempt in range(2):
        try:
            resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.text, url
            print(f"  [{resp.status_code}] {artist} (attempt {attempt+1})")
        except Exception as e:
            print(f"  [error] {artist} attempt {attempt+1}: {e}")
        if attempt == 0:
            time.sleep(2)
    return None, url


NOTE_RE = re.compile(r"^([A-Ga-g][b#]?\d)\s*[-–]\s*([A-Ga-g][b#]?\d)$")


def parse_songs(html: str, requested_artist: str):
    """
    Parse singingcarrots artist-range HTML.
    Structure: <a href="/song?song=...">Title</a> (<span>B2-G4</span>)
    Returns list of {title, low, high} or None on artist mismatch.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Sanity-check: first word of artist name should appear somewhere on page
    first_word = requested_artist.split()[0].lower()
    if first_word not in soup.get_text(" ", strip=True).lower():
        heading = soup.find("h1") or soup.find("h2")
        h_text = heading.get_text() if heading else ""
        print(f"  [mismatch] '{h_text[:60]}' != '{requested_artist}'")
        return None

    songs = []
    for link in soup.find_all("a", href=re.compile(r"/song\?song=")):
        title = link.get_text(strip=True)
        if not title:
            continue
        # The range is in a <span> immediately after the link (text nodes with "(" and ")" surround it)
        for sib in link.next_siblings:
            if hasattr(sib, "name") and sib.name == "span":
                span_text = sib.get_text(strip=True)
                m = NOTE_RE.match(span_text)
                if m:
                    songs.append({
                        "title": title,
                        "low":   normalise_note(m.group(1)),
                        "high":  normalise_note(m.group(2)),
                    })
                break
            # Text node — only skip whitespace/parens; any other content means no span follows
            if hasattr(sib, "strip"):
                t = str(sib).strip(" ()")
                if t:
                    break

    return songs


# ── Fuzzy matching ────────────────────────────────────────────────────────────
def best_match(scraped_title: str, candidates: list):
    """Return (best_candidate, score) or (None, 0) if nothing clears threshold."""
    norm_scraped = normalise_title(scraped_title)
    best_score = 0.0
    best_cand  = None
    for c in candidates:
        score = fuzz.token_sort_ratio(norm_scraped, normalise_title(c))
        if score > best_score:
            best_score = score
            best_cand  = c
    if best_score >= FUZZY_THRESHOLD:
        return best_cand, best_score
    return None, best_score


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    with open(INPUT) as f:
        songs: list[dict] = json.load(f)

    # Index songs by cleaned artist → list of song dicts
    artist_to_songs: dict[str, list[dict]] = {}
    for song in songs:
        raw = song["artist"]
        cleaned = re.sub(r"\s*\(male cover key\)\s*", "", raw, flags=re.IGNORECASE).strip()
        if cleaned in SKIP_ARTISTS:
            continue
        artist_to_songs.setdefault(cleaned, []).append(song)

    artists = sorted(artist_to_songs.keys())
    print(f"Artists to fetch: {len(artists)}  |  Total songs: {len(songs)}")

    verified_rows   = []   # for verified_log.csv
    unmatched_rows  = []   # for unmatched_artists.csv
    verified_count  = 0

    session = requests.Session()

    for i, artist in enumerate(artists, 1):
        print(f"[{i}/{len(artists)}] {artist}", end=" ... ", flush=True)
        time.sleep(REQUEST_DELAY)

        html, url = fetch_artist_page(artist, session)
        if html is None:
            reason = "fetch_failed"
            unmatched_rows.append({
                "artist": artist,
                "song_titles_still_unverified": "; ".join(s["title"] for s in artist_to_songs[artist]),
                "reason": reason,
            })
            print("FAILED")
            continue

        scraped = parse_songs(html, artist)
        if scraped is None:
            reason = "page_mismatch"
            unmatched_rows.append({
                "artist": artist,
                "song_titles_still_unverified": "; ".join(s["title"] for s in artist_to_songs[artist]),
                "reason": reason,
            })
            print("MISMATCH")
            continue

        if not scraped:
            reason = "no_songs_parsed"
            unmatched_rows.append({
                "artist": artist,
                "song_titles_still_unverified": "; ".join(s["title"] for s in artist_to_songs[artist]),
                "reason": reason,
            })
            print("NO_SONGS")
            continue

        # Fuzzy-match scraped songs against our dataset songs for this artist
        my_songs = artist_to_songs[artist]
        my_titles = [s["title"] for s in my_songs]
        matched_mine = set()

        for scraped_song in scraped:
            cand, score = best_match(scraped_song["title"], my_titles)
            if cand is None:
                continue
            # Find the song dict
            for s in my_songs:
                if s["title"] == cand and cand not in matched_mine:
                    matched_mine.add(cand)
                    old_low, old_high = s["low"], s["high"]
                    s["low"]      = scraped_song["low"]
                    s["high"]     = scraped_song["high"]
                    s["verified"] = True
                    verified_count += 1
                    verified_rows.append({
                        "title":      s["title"],
                        "artist":     artist,
                        "old_low":    old_low,
                        "old_high":   old_high,
                        "new_low":    scraped_song["low"],
                        "new_high":   scraped_song["high"],
                        "source_url": url,
                    })
                    break

        unmatched_mine = [s for s in my_songs if s["title"] not in matched_mine and not s.get("verified")]
        if unmatched_mine:
            unmatched_rows.append({
                "artist": artist,
                "song_titles_still_unverified": "; ".join(s["title"] for s in unmatched_mine),
                "reason": f"no_confident_match (scraped {len(scraped)} songs, matched {len(matched_mine)}/{len(my_songs)})",
            })

        print(f"matched {len(matched_mine)}/{len(my_songs)}")

    # ── Write outputs ─────────────────────────────────────────────────────────
    with open(OUTPUT, "w") as f:
        json.dump(songs, f, indent=2)
    print(f"\nWrote {OUTPUT}")

    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title","artist","old_low","old_high","new_low","new_high","source_url"])
        w.writeheader()
        w.writerows(verified_rows)
    print(f"Wrote {LOG}  ({len(verified_rows)} rows)")

    with open(UNMATCHED, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["artist","song_titles_still_unverified","reason"])
        w.writeheader()
        w.writerows(unmatched_rows)
    print(f"Wrote {UNMATCHED}  ({len(unmatched_rows)} rows)")

    # ── Summary ───────────────────────────────────────────────────────────────
    still_unverified = [s for s in songs if not s.get("verified")]
    print("\n" + "="*60)
    print(f"Total songs:        {len(songs)}")
    print(f"Now verified:       {verified_count}")
    print(f"Still estimated:    {len(still_unverified)}")

    # Top 5 artists by unverified count
    unverified_by_artist: dict[str, int] = {}
    for s in still_unverified:
        raw = s["artist"]
        cleaned = re.sub(r"\s*\(male cover key\)\s*", "", raw, flags=re.IGNORECASE).strip()
        unverified_by_artist[cleaned] = unverified_by_artist.get(cleaned, 0) + 1
    top5 = sorted(unverified_by_artist.items(), key=lambda x: -x[1])[:5]
    print("\nTop 5 artists with most unverified songs:")
    for a, cnt in top5:
        print(f"  {a}: {cnt}")
    print("="*60)


if __name__ == "__main__":
    main()

"""
mine_candidates.py
For every artist already in the dataset, scrape ALL their songs from Singing Carrots,
then surface songs that are (a) NOT already in our dataset and (b) fit a baritone range.
Output: candidate_additions.csv  — human-reviewable before adding.
"""

import json, csv, re, time, urllib.parse
import requests
from bs4 import BeautifulSoup
from pathlib import Path

BASE = Path(__file__).parent
JSON_PATH  = BASE / "song-list-export-updated.json"
OUT_CSV    = BASE / "candidate_additions.csv"

REQUEST_DELAY = 1.5
TIMEOUT       = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

FLAT_TO_SHARP = {"Bb":"A#","Db":"C#","Eb":"D#","Gb":"F#","Ab":"G#"}
NOTE_RE = re.compile(r"^([A-Ga-g][b#]?\d)\s*[-–]\s*([A-Ga-g][b#]?\d)$")

# ── Note math ──────────────────────────────────────────────────────────────────
NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

def normalise_note(note):
    note = note.strip()
    for flat, sharp in FLAT_TO_SHARP.items():
        if note.startswith(flat):
            return sharp + note[len(flat):]
    return note

def note_to_midi(note):
    """Convert e.g. 'A2' → MIDI number (C4=60)."""
    m = re.match(r"([A-Ga-g][#b]?)(-?\d)", note)
    if not m:
        return None
    name, octave = m.group(1).upper(), int(m.group(2))
    # normalise flats
    flat_map = {"BB":"A#","DB":"C#","EB":"D#","GB":"F#","AB":"G#"}
    name = flat_map.get(name, name)
    if name not in NOTE_NAMES:
        return None
    return (octave + 1) * 12 + NOTE_NAMES.index(name)

def midi_to_note(midi):
    octave = (midi // 12) - 1
    name   = NOTE_NAMES[midi % 12]
    return f"{name}{octave}"

# Baritone comfort range — generous but filters out clearly tenor/soprano songs
# Low:  A1 (midi 33) – F3 (midi 53)   high note must be <= A4 (midi 69)
# Also require total span >= 8 semitones (a real song, not a single note)
BARI_LOW_MIN  = note_to_midi("A1")   # 33
BARI_LOW_MAX  = note_to_midi("F3")   # 53
BARI_HIGH_MAX = note_to_midi("B4")   # 71  (allow up to B4 — some belting songs)
BARI_HIGH_MIN = note_to_midi("D3")   # 50  (must reach at least D3)
MIN_SPAN      = 8                    # semitones


def is_baritone_range(low_note, high_note):
    l = note_to_midi(low_note)
    h = note_to_midi(high_note)
    if l is None or h is None:
        return False
    if not (BARI_LOW_MIN <= l <= BARI_LOW_MAX):
        return False
    if not (BARI_HIGH_MIN <= h <= BARI_HIGH_MAX):
        return False
    if (h - l) < MIN_SPAN:
        return False
    return True


# ── Scraping ───────────────────────────────────────────────────────────────────
def parse_songs(html):
    soup = BeautifulSoup(html, "html.parser")
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


# Known working URL-name overrides from the retry run
URL_OVERRIDES = {
    "Eagles":                              "The Eagles",
    "Four Tops":                           "The Four Tops",
    "Crosby, Stills & Nash":               "Crosby Stills & Nash",
    "Crosby, Stills, Nash & Young":        "Crosby Stills Nash & Young",
    "Earth, Wind & Fire":                  "Earth Wind & Fire",
    "Edward Sharpe & The Magnetic Zeros":  "Edward Sharpe & the Magnetic Zeros",
    "Harold Melvin & the Blue Notes":      "Harold Melvin and the Blue Notes",
    "Jason Mraz & Colbie Caillat":         "Jason Mraz",
    "Johnny Cash & June Carter":           "Johnny Cash",
    "Peter, Paul and Mary":                "Peter Paul and Mary",
    "Smokey Robinson & The Miracles":      "Smokey Robinson & the Miracles",
    "Nitty Gritty Dirt Band":              "The Nitty Gritty Dirt Band",
    "The Manhattans":                      "Manhattans",
    "Marvin Gaye & Tammi Terrell":         "Marvin Gaye",
    "Bill Withers & Grover Washington Jr.":"Bill Withers",
    "Sinach / Leeland":                    "Sinach",
    "A Little Night Music (male key)":     "A Little Night Music",
    "Les Misérables (Javert)":             "Les Misérables",
    "Toby Keith & Willie Nelson":          "Toby Keith",
}

SKIP_ARTISTS = {"Jazz Standard", "Traditional", "Traditional Gospel", "Traditional Praise"}


def clean_artist(raw):
    return re.sub(r"\s*\(male cover key\)\s*", "", raw, flags=re.IGNORECASE).strip()


def main():
    with open(JSON_PATH) as f:
        songs = json.load(f)

    # Build set of (artist_clean, title_lower) already in dataset
    existing = set()
    artist_titles = {}  # cleaned_artist -> set of lower titles
    for s in songs:
        a = clean_artist(s["artist"])
        t = s["title"].lower().strip()
        existing.add((a, t))
        artist_titles.setdefault(a, set()).add(t)

    # Unique real artists (deduplicated after cleaning)
    all_artists = sorted(set(
        clean_artist(s["artist"]) for s in songs
        if clean_artist(s["artist"]) not in SKIP_ARTISTS
    ))

    session = requests.Session()
    candidates = []

    for i, artist in enumerate(all_artists, 1):
        query = URL_OVERRIDES.get(artist, artist)
        url   = "https://singingcarrots.com/artist-range?artist=" + urllib.parse.quote(query)

        time.sleep(REQUEST_DELAY)
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        except Exception as e:
            print(f"[{i}/{len(all_artists)}] {artist} — ERROR: {e}")
            continue

        if r.status_code != 200:
            print(f"[{i}/{len(all_artists)}] {artist} — {r.status_code}")
            continue

        scraped = parse_songs(r.text)
        if not scraped:
            print(f"[{i}/{len(all_artists)}] {artist} — no songs parsed")
            continue

        # Find songs on site but NOT in our dataset that fit baritone range
        new_count = 0
        for s in scraped:
            t_lower = s["title"].lower().strip()
            if t_lower in artist_titles.get(artist, set()):
                continue  # already in dataset
            if not is_baritone_range(s["low"], s["high"]):
                continue
            candidates.append({
                "title":      s["title"],
                "artist":     artist,
                "low":        s["low"],
                "high":       s["high"],
                "source_url": url,
            })
            new_count += 1

        existing_count = len(artist_titles.get(artist, set()))
        print(f"[{i}/{len(all_artists)}] {artist} — {len(scraped)} on site, {existing_count} in DB, {new_count} new baritone candidates")

    # Sort by artist then title
    candidates.sort(key=lambda x: (x["artist"], x["title"]))

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title","artist","low","high","source_url"])
        w.writeheader()
        w.writerows(candidates)

    print(f"\nTotal baritone-range candidates not in dataset: {len(candidates)}")
    print(f"Written to {OUT_CSV}")

    # Summary by artist
    from collections import Counter
    by_artist = Counter(c["artist"] for c in candidates)
    print("\nTop artists by candidate count:")
    for a, n in by_artist.most_common(15):
        print(f"  {a}: {n}")


if __name__ == "__main__":
    main()

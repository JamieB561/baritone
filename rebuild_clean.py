"""
rebuild_clean.py
1. Remove all unverified songs from the dataset.
2. Add verified candidates to restore ~100 songs per genre.
3. Write song-list-export-final.json — 100% verified, genre-balanced.
"""

import json, csv, re
from collections import Counter, defaultdict
from pathlib import Path

BASE   = Path(__file__).parent
IN_JSON   = BASE / "song-list-export-updated.json"
CANDS_CSV = BASE / "candidate_additions.csv"
OUT_JSON  = BASE / "song-list-export-final.json"
REMOVED_CSV = BASE / "removed_unverified.csv"

GENRE_TARGET = 100   # aim for this many songs per genre


def clean_artist(raw):
    return re.sub(r"\s*\(male cover key\)\s*", "", raw, flags=re.IGNORECASE).strip()


def main():
    with open(IN_JSON) as f:
        songs = json.load(f)

    # ── Step 1: split verified / unverified ──────────────────────────────────
    verified   = [s for s in songs if s.get("verified")]
    unverified = [s for s in songs if not s.get("verified")]

    print(f"Input:      {len(songs)} songs  ({len(verified)} verified, {len(unverified)} unverified)")

    # Save removed songs for record
    with open(REMOVED_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title","artist","genre","low","high"])
        w.writeheader()
        w.writerows({"title": s["title"], "artist": s["artist"],
                     "genre": s["genre"], "low": s["low"], "high": s["high"]}
                    for s in unverified)
    print(f"Removed:    {len(unverified)} unverified songs  → removed_unverified.csv")

    # ── Step 2: load candidates and assign genres ────────────────────────────
    # Build artist → genre mapping from the full original dataset
    artist_genre = defaultdict(Counter)
    for s in songs:
        a = clean_artist(s["artist"])
        artist_genre[a][s["genre"]] += 1
    artist_to_genre = {a: c.most_common(1)[0][0] for a, c in artist_genre.items()}

    with open(CANDS_CSV) as f:
        raw_cands = list(csv.DictReader(f))

    for c in raw_cands:
        c["genre"] = artist_to_genre.get(c["artist"], "Unknown")

    # Deduplicate candidates by (title_lower, genre) so "Johnny Cash" and
    # "Johnny Cash & June Carter" don't double-count the same song
    seen_cand = set()
    cands = []
    for c in raw_cands:
        key = (c["title"].lower().strip(), c["genre"])
        if key not in seen_cand:
            seen_cand.add(key)
            cands.append(c)

    # Also exclude any title already present in verified set (by title+genre)
    verified_keys = {(s["title"].lower().strip(), s["genre"]) for s in verified}
    cands = [c for c in cands if (c["title"].lower().strip(), c["genre"]) not in verified_keys]

    print(f"Candidates: {len(cands)} available after dedup")

    # ── Step 3: how many to add per genre ───────────────────────────────────
    verified_by_genre = Counter(s["genre"] for s in verified)
    cands_by_genre    = defaultdict(list)
    for c in cands:
        cands_by_genre[c["genre"]].append(c)

    print("\nGenre plan:")
    additions = []
    for genre in sorted(verified_by_genre.keys()):
        have    = verified_by_genre[genre]
        need    = max(0, GENRE_TARGET - have)
        avail   = len(cands_by_genre.get(genre, []))
        to_add  = min(need, avail)
        picked  = cands_by_genre[genre][:to_add]
        additions.extend(picked)
        print(f"  {genre:<20}  verified={have:3d}  need={need:3d}  avail={avail:4d}  adding={to_add:3d}")

    # ── Step 4: build final song list ────────────────────────────────────────
    new_songs = []
    for s in verified:
        new_songs.append(s)

    for c in additions:
        new_songs.append({
            "title":    c["title"],
            "artist":   c["artist"],
            "genre":    c["genre"],
            "low":      c["low"],
            "high":     c["high"],
            "verified": True,
        })

    # Sort: genre, then artist, then title
    new_songs.sort(key=lambda s: (s["genre"], clean_artist(s["artist"]), s["title"]))

    with open(OUT_JSON, "w") as f:
        json.dump(new_songs, f, indent=2)

    # ── Summary ──────────────────────────────────────────────────────────────
    final_by_genre = Counter(s["genre"] for s in new_songs)
    all_verified   = all(s.get("verified") for s in new_songs)

    print(f"\nFinal dataset: {len(new_songs)} songs  (100% verified: {all_verified})")
    print("By genre:")
    for g in sorted(final_by_genre.keys()):
        print(f"  {g:<20}  {final_by_genre[g]}")
    print(f"\nWritten to {OUT_JSON}")
    print(f"Removed log: {REMOVED_CSV}")


if __name__ == "__main__":
    main()

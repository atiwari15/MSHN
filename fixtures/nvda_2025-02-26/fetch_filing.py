"""
Download the real 8-K and its exhibits for the NVDA Q4 FY2025 fixture,
straight from SEC EDGAR. This is a throwaway-simple preview of what
edgar/client.py will do properly later:

  - sends a descriptive User-Agent (mandatory; missing it -> HTTP 403)
  - stays well under the 10 req/sec fair-access limit
  - resolves exhibit filenames from index.json instead of guessing them

Fill in YOUR_UA below with your app name + contact email before running.
"""

import json
import time
import pathlib
import urllib.request

# EDGAR REQUIRES a descriptive User-Agent naming your app and a contact email.
# Requests without it get 403'd. Replace this before running.
YOUR_UA = "MSHN/0.1 (you@example.com)"

ACCESSION_DIR = (
    "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000021"
)
HERE = pathlib.Path(__file__).parent

# Polite pause between requests. The hard limit is 10/sec across all EDGAR
# domains; ~8/sec (0.13s) is a safe ceiling. We fetch a handful of files, so
# a flat 0.2s is more than enough.
REQUEST_SPACING_SECONDS = 0.2


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": YOUR_UA})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    time.sleep(REQUEST_SPACING_SECONDS)
    return data


def main() -> None:
    # 1) Pull the filing directory index to discover exhibit filenames.
    #    We do NOT hardcode the 99.2 name - we look it up, the same rule the
    #    live client must follow.
    index = json.loads(get(f"{ACCESSION_DIR}/index.json").decode("utf-8"))
    items = index["directory"]["item"]

    # Grab the .htm documents (body 8-K + exhibits). Skip XBRL/cover junk.
    wanted = [
        it["name"]
        for it in items
        if it["name"].endswith(".htm") and not it["name"].endswith("index.htm")
    ]

    print("Found documents in the filing:")
    for name in wanted:
        print(f"  - {name}")

    # 2) Download each one into this fixture folder.
    for name in wanted:
        blob = get(f"{ACCESSION_DIR}/{name}")
        (HERE / name).write_bytes(blob)
        print(f"saved {name} ({len(blob):,} bytes)")

    print("\nDone. The press release (q4fy25pr.htm) is the main text your")
    print("retrieve-and-explain loop should be able to cite.")


if __name__ == "__main__":
    main()

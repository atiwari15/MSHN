# Fixture: aapl_2025-05-12

Apple rose **~6.2%** on 2025-05-12 ($198.53 → $210.79) as part of a market-wide rally on the U.S.–China 90-day tariff truce — the S&P 500 gained 3.26% and the Nasdaq 4.35% that session, with Tesla, Nvidia and Apple all up sharply. The cause is macro and sector-wide.

Apple *did* file an 8-K that same day, and this fixture deliberately indexes it: accession `0001140361-25-018400`, Item 8.01, carrying an underwriting agreement (EX-1.1), an indenture (EX-4.1) and a legal opinion (EX-5.1). It is a **routine notes offering** — treasury activity that does not move a megacap 6%.

That combination is what makes this the **hard honesty case**. `coin_2026-06-05` has no filing at all, so retrieval comes back empty and `explain()` short-circuits without ever calling the model. Here retrieval returns real, recent, same-day company documents with strong recency weighting, and the system must still conclude they don't explain the move. It tests whether "no clear catalyst found" is genuine judgment or just an artifact of an empty index.

It is also the first **up move** in the fixture set.

Run `python fixtures/fetch.py aapl_2025-05-12` to download the filing.

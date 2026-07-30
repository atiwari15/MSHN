# MSHN

A live-RAG market anomaly explainer. It watches a small stock watchlist for unusual price moves, and when one fires, retrieves relevant recent SEC 8-K filings and generates a grounded, cited explanation of the likely cause — or honestly reports "no clear catalyst found" when there isn't one.

Two independent loops drive it: a price loop (the trigger) and a filing-ingestion loop (the knowledge base), feeding a shared, timestamped vector store.

See `fixtures/nvda_2025-02-26/` for the first offline test case (Stage 1): NVIDIA's Q4 FY2025 "beat but the stock fell" event.

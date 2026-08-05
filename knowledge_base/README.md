# ACME Corp Knowledge Base

Put your organization's documents here. Supported formats:

- `.txt` — plain text files
- `.md` / `.markdown` — Markdown files
- `.xlsx` / `.xlsm` — Excel files (one searchable block per data row;
  the first row of each sheet must be the column header)
- `.pdf` — text-based PDFs

Then run:

    python scripts/ingest_cli.py knowledge_base/

The files are chunked and embedded into the local LanceDB table. Add more
documents any time and re-run the ingestion to scale the knowledge base.

See `docs/ADD_KNOWLEDGE.md` for the full procedure (including Excel layout
rules, verification and troubleshooting).

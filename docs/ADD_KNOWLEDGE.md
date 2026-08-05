# How to Add Knowledge — Excel, Markdown, Text

This procedure explains how to grow the bot's knowledge base using any of the
supported sources:

| Format | Extension | How it is read |
|---|---|---|
| Text | `.txt` | The whole file is read as plain text |
| Markdown | `.md`, `.markdown` | The whole file is read as plain text (headings/formatting are kept as text) |
| Excel | `.xlsx`, `.xlsm` | Each non-empty **data row** becomes one searchable block |
| PDF | `.pdf` | Text is extracted page by page (see limitations below) |

> **Do not edit the code.** Everything below uses the ingestion command and the
> `knowledge_base/` folder.

- [1. Where the documents live](#1-where-the-documents-live)
- [2. The ingestion command](#2-the-ingestion-command)
- [3. Adding a text file (.txt)](#3-adding-a-text-file-txt)
- [4. Adding a Markdown file (.md)](#4-adding-a-markdown-file-md)
- [5. Adding an Excel file (.xlsx)](#5-adding-an-excel-file-xlsx)
- [6. PDFs and limitations](#6-pdfs-and-limitations)
- [7. Verifying the knowledge was added](#7-verifying-the-knowledge-was-added)
- [8. Best practices for good answers](#8-best-practices-for-good-answers)
- [9. Troubleshooting](#9-troubleshooting)

---

## 1. Where the documents live

Put your source files in the **`knowledge_base/`** folder at the root of the
project (sub-folders are allowed). When you ingest, only the supported
extensions are picked up — other files (images, `.csv`, `.docx`, …) are
ignored.

```
knowledge_base/
├── welcome.md              <- Markdown
├── faq.txt                 <- Text
├── pricing.xlsx            <- Excel
└── policies/               <- sub-folder
    └── returns.txt
```

## 2. The ingestion command

After adding or editing documents, run ingestion **once**:

```bash
# Running via Docker (server)
docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/

# Running locally (without Docker)
python scripts/ingest_cli.py knowledge_base/
```

What it does, in order:

1. Scans the folder for supported files.
2. Reads each file (Excel → one block per row).
3. Splits each block into chunks (~600 characters, 100 overlap).
4. Converts each chunk into a vector with the embedding model (`bge-m3`).
5. Stores the vectors + text in the LanceDB table (`data/lancedb`).

Useful variations:

```bash
# ingest a single file
docker compose exec chatbot python scripts/ingest_cli.py pricing.xlsx

# ingest a single sub-folder
docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/policies/

# wipe the table and rebuild from scratch (e.g. after changing the embedding model)
docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/ --reset
```

> Re-run ingestion after **every edit** to a source document. The bot only
> knows what was ingested, not what sits in the folder.

## 3. Adding a text file (.txt)

1. Create/edit the file, e.g. `knowledge_base/faq.txt`.
2. Use one topic per paragraph and separate paragraphs with a blank line:

   ```
   Return policy: products can be returned within 30 days of purchase
   in their original packaging. Refunds are issued within 10 business days.

   Shipping: orders placed before 14:00 ship the same day. Standard
   delivery takes 3 to 5 business days.
   ```

3. Save the file as **UTF-8** (Notepad/VS Code default; avoid "ANSI").
4. Run the ingestion command (section 2).

## 4. Adding a Markdown file (.md)

Same as text, with the freedom to use headings, lists, tables and links — they
are all read as text.

1. Create/edit the file, e.g. `knowledge_base/welcome.md`.
2. Keep headings short and descriptive. The bot answers best with full
   sentences and plain statements:

   ```markdown
   # Support hours

   Our support team is available Monday to Friday from 9:00 to 18:00.
   Messages sent on weekends are answered within 24 hours.

   # Contact

   - Email: support@acme.example.com
   - Phone: +1 555 123 4567
   ```

3. Save and run the ingestion command (section 2).

> Tables in Markdown are fine — each row is kept together in the same chunk.

## 5. Adding an Excel file (.xlsx)

Excel files are the best choice for **structured data** (price lists,
catalogues, FAQs with columns, employee directories, error codes, …).

### 5.1 Rules for the spreadsheet

| Rule | Why |
|---|---|
| **Row 1 of each sheet must be the header** (column titles) | The header is used to label every cell value |
| **One row = one fact / one record** | Each row becomes its own searchable block |
| Keep cells concise; avoid merging cells and images | Merged cells may be read as empty |
| Use only the first sheet if the rest are drafts | Every sheet is ingested |
| Save as **`.xlsx`** | Legacy `.xls` files are not supported (re-save them as `.xlsx` in Excel) |
| Remove sensitive/irrelevant columns or rows | Everything non-empty is ingested |

### 5.2 What a good sheet looks like

| Product | Price | Warranty | Details |
|---|---|---|---|
| Premium Widget Pro | 129.99 | 2 years | Black, white or silver. Includes premium support. |
| Standard Widget | 49.99 | 1 year | Available in blue or red. |

That sheet becomes two searchable blocks:

```
[sheet 'Sheet1', row 2] Product: Premium Widget Pro | Price: 129.99 | Warranty: 2 years | Details: ...
[sheet 'Sheet1', row 3] Product: Standard Widget | Price: 49.99 | Warranty: 1 year | Details: ...
```

### 5.3 Recommended layout for an FAQ in Excel

| Question | Answer |
|---|---|
| What is your return policy? | Products can be returned within 30 days... |
| How long does shipping take? | Orders placed before 14:00 ship the same day... |
| How do I contact support? | Email support@acme.example.com... |

The bot matches a customer's question to the closest `Question`/`Answer` pair.

### 5.4 Steps

1. Prepare the sheet following 5.1 and 5.2.
2. Save it as `knowledge_base/<name>.xlsx` (File → **Save As** → Excel Workbook).
3. Run the ingestion command (section 2).

## 6. PDFs and limitations

- PDFs are read with a text extractor. Text-based PDFs work well; **scanned PDFs
  (images) produce no text** and are silently skipped with a warning.
- Keep tables simple — complex PDF tables can lose their alignment.

## 7. Verifying the knowledge was added

1. Check the chunk count in the health endpoint:

   ```bash
   curl http://localhost:8000/health
   # {"status":"ok","chunks":42}
   ```

   The count is the number of **chunks**, not files — a long file produces
   several chunks.

2. Watch the ingestion log (Docker):

   ```bash
   docker compose logs -f chatbot
   # INFO ... Ingested 32/128 chunks
   # Ingested 128 chunks from 5 files into 'knowledge_base'
   ```

3. Ask the bot a question that only the new document can answer (send a message
   on WhatsApp, or test the AI directly):

   ```bash
   curl http://localhost:8001/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model":"qwen3.5:4b","messages":[{"role":"user","content":"<your question>"}]}'
   ```

## 8. Best practices for good answers

- **Write full sentences**, not fragments or abbreviations. The bot answers in
  the same style as your documents.
- **One idea per paragraph** (or per Excel row). Retrieval works on ~600
  character chunks — keep related facts together but separate topics apart.
- **Repeat key terms** naturally: a chunk that says "the device" without naming
  the product is harder to match to a customer question that names it.
- **Avoid dates and numbers that go stale** (prices, phone numbers) unless you
  plan to keep the files up to date. Re-ingest after updates.
- **Don't dump raw logs or huge tables.** Truncate long text to what a customer
  would realistically ask about.
- **Keep the total manageable.** ~500 chunks is comfortable; thousands still
  work but retrieval stays fast because search is vector-based.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "No supported documents found" | Folder is empty or only unsupported files | Check extensions (`.txt/.md/.pdf/.xlsx`); no `.csv`/`.docx` |
| Excel rows are missing | Sheets have no header row, or rows are merged/empty | Add a header row; unmerge cells; remove blank rows |
| "Excel support requires openpyxl" | Container image is outdated | Rebuild: `docker compose build chatbot && docker compose up -d` |
| Scanned PDF yields nothing | PDF is image-only | Convert/OCR the PDF, or copy the text into a `.txt`/`.md` |
| Chunks count didn't change | Ingestion not re-run, or the file was already counted | Re-run the ingestion command; check the log line |
| "Could not read <file>" in logs | Corrupt or unsupported file | Check the file opens normally; re-save it |
| Wrong answers after adding docs | Threshold too low or stale vectors | Raise `knowledge.score_threshold` in `config.yaml`; if you changed the embedding model, re-ingest with `--reset` |

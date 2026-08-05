# Google service-account credentials

Put the service-account JSON key for the conversation registry here as
`gspread-sa.json` (the default path configured in `config/config.yaml`).

This folder is mounted read/write into the container at `/app/credentials` by
`docker-compose.yml`, so the key does not need to be copied into the image.

Never commit the key to version control.

See `docs/CONVERSATION_REGISTRY.md` → *Google Sheets backend* for how to create
the service account and share the spreadsheet with it.

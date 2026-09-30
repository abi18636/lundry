# Security

If API tokens or exchange secrets were pasted in chat, **rotate them now**:

1. GitHub: revoke the PAT and create a fine-scoped new one (repo only).
2. Render: rotate API key if exposed.
3. Deribit: regenerate Client Secret on testnet.

This repository must never contain `.env` or live secrets.

# Security Policy

## Supported configuration

Keep credentials in a local `.env` file or in environment variables. Never place API keys, access tokens, cookies, or passwords in YAML configuration files, source files, tests, screenshots, or generated evidence.

`.env`, `.env.*`, browser downloads, and generated `output/` directories are ignored by Git. The committed `.env.example` file intentionally contains no value.

## Reporting a vulnerability

If you discover a security issue or suspect that a credential has been exposed, do not open a public issue containing the secret. Contact the repository maintainer privately, rotate the affected credential, and include only redacted details in any public follow-up.

# Security

Report vulnerabilities privately through GitHub's security advisory feature for this repository. Do not publish credentials or Home Assistant configuration in issues.

Only the latest release is supported. The integration has no brokerage account access or trading endpoints. It uses Home Assistant's authenticated WebSocket with an entity read-permission check, validates instrument IDs, and contacts only fixed public Avanza market-data URLs. Redirects, oversized responses and invalid data are rejected. Requests and memory caches are bounded; errors and rate limits are cached to reduce retries.

GitHub Actions use read-only default permissions and pinned action revisions. Changes go through protected-branch pull requests and automated tests, HACS, Hassfest and CodeQL validation.

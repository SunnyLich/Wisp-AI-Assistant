# Test catalogue

`test_map.json` is the authoritative classification and scheduling catalogue for every automated test file and manual diagnostic test utility.

Each entry has two required axes:

- behaviour: `internal` or `user_path`
- execution: `github_safe` or `isolated_host`

Together they produce GI, GU, II, or IU. Scope and platform metadata are separate and do not replace either required axis.

Regenerate after adding, moving, or intentionally reclassifying tests:

```powershell
python scripts/test_map.py
```

Validate without writing:

```powershell
python scripts/test_map.py --check
```

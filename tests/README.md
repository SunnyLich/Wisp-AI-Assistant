# Test structure

Tests use two required classification axes recorded in `catalog/test_map.json`:

| | Internal | User path |
|---|---|---|
| GitHub-safe | GI | GU |
| Isolated host | II | IU |

The target physical scopes are:

- `unit/`: pure logic with no subsystem runtime
- `component/`: one subsystem with controlled boundaries
- `integration/`: workers, IPC, persistence, providers, packaging
- `ui/`: real Qt widgets driven through Qt events
- `e2e/`: multi-feature user workflows through production entry points
- `native/`: operating-system adapters and contracts
- `support/`: harness, runner, build, and test-infrastructure checks
- `catalog/`: test and feature coverage catalogues

Migration is incremental. The catalogue and enforcement gate are established before files move, so a rename cannot silently remove a test from local or CI execution.

Completed physical moves:

- brain worker and handler tests: `integration/brain/`
- catalogue and coverage validators: `catalog/`
- runner, build, dependency, cleanup, and scanner tests: `support/`

`isolated_host` tests are skipped unless `WISP_ISOLATED_TEST_HOST=1` is set. That flag belongs only on disposable VMs or dedicated self-hosted test machines.

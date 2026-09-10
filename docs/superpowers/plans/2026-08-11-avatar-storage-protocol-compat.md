# Avatar Storage and Protocol Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the deployed audio SPIFFS partition while relocating the expanded avatar pack, make legacy avatar fallback an informational migration, and document the connected and broadcast profile protocols exactly.

**Architecture:** Keep the writable audio partition at its historical offset and place the read-only 1MB avatar image after it. Treat a missing saved avatar as a successful catalog migration. Maintain separate public specifications for connected JSON configuration and signed gateway broadcasts.

**Tech Stack:** ESP-IDF 5.5.3, C, CSV partition table, Python unittest, Markdown.

## Global Constraints

- `audio` remains at `0x37a000`, size `0x80000`.
- `imgava` is placed at `0x3fa000`, size `0x100000`.
- Connected BLE transfers only `avatar_name`, never avatar PNG data.
- Gateway v1 token broadcasts remain backward compatible.
- Gateway v2 profile broadcasts use the firmware's existing 26-byte `0x23` format.
- User avatar asset additions and deletions are not modified by these tasks.

---

### Task 1: Preserve the audio partition

**Files:**
- Modify: `partitions.csv`
- Create: `tools/test_partition_layout.py`

**Interfaces:**
- Consumes: ESP-IDF CSV partition layout.
- Produces: an upgrade-compatible audio address and a non-overlapping 1MB avatar partition.

- [ ] Write a Python test that parses `partitions.csv`, resolves blank offsets from `0x9000` with `0x1000` alignment for app/data partitions, and asserts literal resolved ranges: `audio=[0x37a000,0x3fa000)` and `imgava=[0x3fa000,0x4fa000)`.
- [ ] Run `python3 -m unittest tools.test_partition_layout -v`; verify RED because `imgava` currently precedes and moves `audio`.
- [ ] Reorder the final two partition rows to `audio` then `imgava`, retaining their sizes.
- [ ] Re-run the focused test and verify GREEN.
- [ ] Commit the test and partition change as `fix(storage): preserve audio partition across avatar upgrade`.

### Task 2: Classify legacy avatar fallback as migration

**Files:**
- Modify: `components/app/src/app.c`
- Modify: `components/app/test/test_avatar_profile_contract.py`

**Interfaces:**
- Consumes: `avatar_store_has`, `avatar_store_first_name`, `save_profile`.
- Produces: the same fallback and persistence behavior with INFO diagnostics for the expected migration path.

- [ ] Add a contract test that requires the missing-saved-avatar branch to call `ESP_LOGI` and still copy the first catalog name and call `save_profile`.
- [ ] Run the focused contract test; verify RED while the branch uses `ESP_LOGW`.
- [ ] Change only the successful fallback message from `ESP_LOGW` to `ESP_LOGI`; retain the warning for save failure.
- [ ] Re-run the avatar application contract suite and verify GREEN.
- [ ] Commit as `fix(app): classify avatar fallback as migration`.

### Task 3: Synchronize both public protocols

**Files:**
- Modify: `doc/ble-protocol-spec.md`
- Modify: `doc/ble-broadcast-protocol-for-gateway.md`

**Interfaces:**
- Consumes: `config_json.c`, `profile_bcast.h`, `profile_bcast.c`, and `app.c` as authoritative implemented behavior.
- Produces: implementable connected-client and Android gateway specifications.

- [ ] Update the connected BLE section to identify the mini-program role and specify `nickname`, `avatar_name`, `token`, `token_max`, and `time`, including persistence and unknown-avatar behavior.
- [ ] Add a gateway v2 section with the literal 26-byte layout: `HB`, `0x23`, field, target, u16 transaction, fragment index/count/total length, 3 data bytes, and 8-byte HMAC over bytes `[0..17]`.
- [ ] Document field IDs 1–5 and their exact shapes: u32 LE token, u32 LE positive token_max, u32 LE positive time, 1–47-byte UTF-8 nickname, and 1–15-byte catalog avatar name.
- [ ] Document fragment count `ceil(total_len/3)`, zero padding, 3-second reassembly timeout, `(field,txn)` deduplication, resend behavior, and catalog JSON ownership.
- [ ] Update document version/change history and run `git diff --check`.
- [ ] Commit as `docs: specify connected and broadcast profile updates`.

### Task 4: Full regression and firmware verification

**Files:**
- Verify only; do not stage `assets/avatars` changes.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: test and build evidence plus correct flash offsets.

- [ ] Run the partition, avatar pack, application contract, and host protocol Python suites.
- [ ] Run the focused C service tests that include profile broadcast and avatar pack cases.
- [ ] Run `idf.py build` with ESP-IDF 5.5.3.
- [ ] Verify build output flashes `audio` at `0x37a000` only when explicitly packaged and `imgava.bin` at `0x3fa000`; verify app size fits.
- [ ] Run `git diff --check` and inspect `git status --short` to confirm only user avatar assets remain unstaged.

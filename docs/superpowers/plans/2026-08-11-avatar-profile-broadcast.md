# Local Avatar Pack and Profile Broadcast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a dynamic nonempty catalog in an `imgava` partition sized for at least ten compliant PNG avatars, select them by name over BLE or signed v2 broadcasts, and remove runtime BLE avatar-image upload while retaining v1 token broadcasts, fullscreen upload, and screen capture.

**Architecture:** A host packer creates a validated `AVA1` read-only image from `assets/avatars`; firmware mmap-accesses it through a pure parser and reuses the existing PNG-to-`imgframe` decoder. Profile storage gains an avatar name with v13 migration. A separate v2 broadcast assembler authenticates and atomically reconstructs one profile field per transaction, while the application applies completed values through existing persistence and capture-safe UI paths.

**Tech Stack:** C11, ESP-IDF 5.5.3, LVGL 9.5, NimBLE, Python 3, Pillow, built-in image generation, Unity, `unittest`.

## Global Constraints

- Preserve the existing 26-byte v1 token broadcast byte-for-byte and behavior-for-behavior.
- The v2 manufacturer payload is exactly 26 bytes and follows the layout in the approved design.
- HMAC remains an 8-byte truncation of HMAC-SHA256; each v2 fragment authenticates bytes `0..17`.
- Avatar names are 1–15 bytes and match `[a-z0-9_-]+`; `default` is mandatory.
- Ship at least ten `96×156`, RGBA8, non-interlaced PNG avatars in `assets/avatars/`.
- The `imgava` partition is `0x100000` bytes and becomes a read-only dynamic `AVA1` pack.
- BLE image-data upload becomes fullscreen-only; screen capture and fullscreen upload remain supported.
- Unknown or corrupt avatars never overwrite the saved name or clear the currently displayed avatar.
- Do not block the NimBLE host task on LVGL during screen capture.

---

### Task 1: Generate and normalize ten avatar assets

**Files:**
- Create: `assets/avatars/default.png`
- Create: `assets/avatars/neon_cat.png`
- Create: `assets/avatars/cyber_fox.png`
- Create: `assets/avatars/holo_bear.png`
- Create: `assets/avatars/mint_robot.png`
- Create: `assets/avatars/amber_bot.png`
- Create: `assets/avatars/violet_owl.png`
- Create: `assets/avatars/aqua_panda.png`
- Create: `assets/avatars/red_rabbit.png`
- Create: `assets/avatars/blue_wolf.png`
- Create: `tools/normalize_avatar.py`
- Create: `tools/test_normalize_avatar.py`

**Interfaces:**
- Produces ten decoder-compatible PNG inputs consumed by Task 2.

- [ ] **Step 1: Write failing normalizer tests**

Use Pillow-created fixtures to assert `normalize_avatar.py INPUT OUTPUT` produces exactly `(96, 156)`, mode `RGBA`, `interlace=0`, preserves transparent corners, and rejects a fully opaque/non-removable background when `--require-alpha` is used.

- [ ] **Step 2: Verify RED**

Run `python3 -m unittest tools/test_normalize_avatar.py -v`; expect import/command failures because the normalizer is absent.

- [ ] **Step 3: Generate the source portraits**

Use the `imagegen` skill in built-in mode, one call per distinct avatar. Every prompt must use this shared specification:

```text
Use case: stylized-concept
Asset type: embedded-device profile avatar
Primary request: one cyberpunk pixel-art half-body portrait, centered and fully visible
Scene/backdrop: perfectly flat solid #00FF00 chroma-key background
Style/medium: crisp pixel-art illustration with large readable shapes
Composition/framing: portrait, generous padding, no cropped head or shoulders
Constraints: no text, no logo, no watermark, no shadow; do not use #00FF00 in the subject
```

Add the following subject line respectively: green friendly robot (`default`), neon cat hacker, cyber fox scout, holographic bear guardian, mint service robot, amber industrial bot, violet owl analyst, aqua panda engineer, red rabbit courier, blue wolf ranger.

- [ ] **Step 4: Normalize into project assets**

Implement the normalizer with Pillow: convert to RGBA, remove the flat border-sampled chroma key with soft alpha, contain-fit into a transparent `96×156` canvas using nearest-neighbor scaling, and save an 8-bit non-interlaced RGBA PNG. Inspect all ten final files visually and verify no green fringe or accidental transparency through the subject.

- [ ] **Step 5: Verify and commit**

Run `python3 -m unittest tools/test_normalize_avatar.py -v`, then a Pillow script asserting all ten names, sizes, modes and alpha extrema. Commit as `feat(assets): add built-in avatar catalog`.

---

### Task 2: Build and flash the AVA1 resource pack

**Files:**
- Create: `tools/avatar_pack.py`
- Create: `tools/test_avatar_pack.py`
- Modify: `CMakeLists.txt`
- Modify: `scripts/release.py`

**Interfaces:**
- Produces `${CMAKE_BINARY_DIR}/imgava.bin` with a 16-byte header and fixed 28-byte entries.
- Header: `magic[4]="AVA1"`, `version u8=1`, `count u8`, `header_size u16 LE`, `total_size u32 LE`, `index_crc32 u32 LE`.
- Entry: `name[16]` NUL-padded, `offset u32 LE`, `length u32 LE`, `crc32 u32 LE`.

- [ ] **Step 1: Write failing packer tests**

Tests must build temporary RGBA PNG catalogs and assert dynamic inclusion, deterministic ordering by name, four-byte data alignment, correct header/index CRCs, per-PNG CRCs, `0xFF` partition padding, and rejection of missing `default`, fewer than ten files, duplicate/case-colliding names, invalid names, wrong dimensions/mode/interlace, corrupt PNG, and overflow.

- [ ] **Step 2: Verify RED**

Run `python3 -m unittest tools/test_avatar_pack.py -v`; expect import failure.

- [ ] **Step 3: Implement the packer**

Expose `build_pack(input_dir: Path, partition_size: int) -> bytes` and CLI arguments `--input`, `--output`, `--size`. Use `zlib.crc32(...) & 0xffffffff`. Refuse to overwrite an output outside the requested path and write atomically through a sibling temporary file plus `os.replace`.

- [ ] **Step 4: Integrate build and flash**

After `project(trae_card)` in the top-level CMake file, query the `imgava` offset/size, add an `avatar_pack_bin ALL` target invoking the packer, register `${CMAKE_BINARY_DIR}/imgava.bin` with `esptool_py_flash_target_image(flash ...)`, and make `flash` depend on the pack target.

Update `scripts/release.py` to append the `imgava` offset and binary to `merge_bin`, include `imgava.bin` in the ZIP, and update the manifest so only `imgstore`/`imgframe` remain runtime image data.

- [ ] **Step 5: Verify and commit**

Run packer tests, `idf.py build`, inspect `build/flash_args` for `imgava`, run `python3 scripts/release.py`, and use `esptool.py image_info`/byte-offset inspection to prove the merged image contains `AVA1` at the partition offset. Commit as `feat(build): package named avatars in imgava`.

---

### Task 3: Parse and expose named avatars on firmware

**Files:**
- Create: `components/core/services/include/services/avatar_pack.h`
- Create: `components/core/services/src/avatar_pack.c`
- Create: `components/core/services/test/test_avatar_pack.c`
- Modify: `components/core/services/CMakeLists.txt`
- Modify: `components/core/services/test/CMakeLists.txt`
- Create: `components/platform/platform_esp32/src/avatar_store.h`
- Create: `components/platform/platform_esp32/src/avatar_store.c`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

**Interfaces:**

```c
typedef struct { const uint8_t *base; size_t size; } avatar_pack_t;
int avatar_pack_init(avatar_pack_t *pack, const uint8_t *base, size_t size);
int avatar_pack_find(const avatar_pack_t *pack, const char *name,
                     const uint8_t **png, size_t *png_len);

int avatar_store_init(void);
bool avatar_store_has(const char *name);
int avatar_store_get(const char *name, const uint8_t **png, int *len);
void avatar_store_deinit(void);
```

- [ ] **Step 1: Write failing Unity parser tests**

Use literal AVA1 fixtures to cover valid lookup, unknown name, bad magic/version/count/header size/total size/index CRC, unterminated or invalid names, overlapping/out-of-range entries, PNG CRC mismatch, duplicate names and null arguments.

- [ ] **Step 2: Verify RED**

Build the focused host harness or `host_test`; expect missing symbols.

- [ ] **Step 3: Implement parser and ESP32 adapter**

The core parser performs overflow-safe bounds checks and no allocation. `avatar_store_init` finds `imgava`, mmap-maps the whole partition once, initializes the parser and keeps the mapping alive until deinit. Lookup verifies the selected entry CRC before returning its flash pointer.

- [ ] **Step 4: Verify and commit**

Run focused parser tests, the standard services tests where available, and `idf.py build`. Commit as `feat(avatar): read named flash avatar pack`.

---

### Task 4: Persist avatar names and select them over BLE JSON

**Files:**
- Modify: `components/core/services/include/services/profile_ctl.h`
- Modify: `components/core/services/src/profile_ctl.c`
- Modify: `components/core/services/test/test_profile_ctl.c`
- Modify: `components/core/services/include/services/config_json.h`
- Modify: `components/core/services/src/config_json.c`
- Modify: `components/core/services/test/test_config_json.c`
- Modify: `components/core/ports/include/hal/hal_config.h`

**Interfaces:**
- Append `char avatar_name[16]` to `profile_data_t` and set `PROFILE_BLOB_VER` to 14.
- `cfg_changed_t` gains `bool avatar_name`; both JSON keys `nickname` and `name` target `profile_data_t.name`.
- Add `CFG_ST_ERR_AVATAR 0x1D`.

- [ ] **Step 1: Write failing profile/config tests**

Assert the v14 default `avatar_name="default"`, v14 round trip, exact migration from a literal v13-layout blob with all old fields preserved, rejection of other versions/lengths, `nickname`/legacy `name` behavior, valid `avatar_name`, and UTF-8/name truncation boundaries.

- [ ] **Step 2: Verify RED**

Run focused profile and config tests; expect missing fields/flags.

- [ ] **Step 3: Implement schema and migration**

Define a private v13 layout struct matching the old field order. For v13, initialize v14 defaults, copy every legacy field explicitly, and leave `avatar_name` as `default`; do not memcpy a shorter struct over the new struct. Parse `nickname` before legacy `name` and define that if both occur, `nickname` wins.

- [ ] **Step 4: Verify and commit**

Run focused tests and `idf.py build`. Commit as `feat(profile): persist named avatar selection`.

---

### Task 5: Add the authenticated v2 profile broadcast assembler

**Files:**
- Create: `components/core/services/include/services/profile_bcast.h`
- Create: `components/core/services/src/profile_bcast.c`
- Create: `components/core/services/test/test_profile_bcast.c`
- Modify: `components/core/services/CMakeLists.txt`
- Modify: `components/core/services/test/CMakeLists.txt`
- Modify: `components/core/services/include/services/ble_match.h`
- Modify: `components/core/services/src/ble_match.c`
- Modify: `components/core/services/test/test_ble_match.c`

**Interfaces:**

```c
#define PROFILE_BCAST_LEN 26
#define PROFILE_BCAST_TIMEOUT_MS 3000u
typedef enum { PB_FIELD_TOKEN=1, PB_FIELD_TOKEN_MAX=2, PB_FIELD_TIME=3,
               PB_FIELD_NICKNAME=4, PB_FIELD_AVATAR_NAME=5 } pb_field_t;
typedef enum { PB_MORE, PB_COMPLETE, PB_IGNORE, PB_ERROR } pb_status_t;
typedef struct { pb_field_t field; uint8_t data[47]; uint8_t len; uint16_t txn; } pb_result_t;
void profile_bcast_init(profile_bcast_t *s, const uint8_t target[6],
                        token_mac_fn mac, void *mac_user);
pb_status_t profile_bcast_feed(profile_bcast_t *s, const uint8_t frame[26],
                               uint32_t now_ms, pb_result_t *out);
```

- [ ] **Step 1: Write failing v2 tests**

Build signed 26-byte literal frames and cover all five fields, two-fragment uint32 values, 16-fragment 47-byte nickname, five-fragment avatar name, out-of-order fragments, exact duplicates, conflicting duplicates, wrong target/HMAC/header/field/count/index/total length/padding, transaction replacement, 3-second timeout with uint32 wrap, completed-transaction replay and atomic output only on completion.

- [ ] **Step 2: Verify RED**

Run focused v2 tests; expect missing header/symbols.

- [ ] **Step 3: Implement assembler and classifier**

Use a fixed 48-byte assembly buffer and bit mask; allocate no heap. Authenticate before accepting fragment data. Classify `HB 0x23` as a new `BLE_MATCH_PROFILE` while retaining the exact v1 `BLE_MATCH_TOKEN` classification.

- [ ] **Step 4: Verify v1 compatibility and commit**

Run every existing `test_token_bcast.c` case unchanged plus new profile-broadcast and matcher tests. Commit as `feat(ble): add signed profile broadcasts`.

---

### Task 6: Integrate named avatar display and profile updates in the app

**Files:**
- Modify: `components/platform/platform_esp32/src/jpeg_store.h`
- Modify: `components/platform/platform_esp32/src/jpeg_store.c`
- Modify: `components/platform/platform_esp32/src/jpeg_view.c`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Modify: `components/app/src/app.c`
- Modify: `components/app/test/test_screen_capture_contract.py`
- Create: `components/app/test/test_avatar_profile_contract.py`

**Interfaces:**

```c
void jpeg_view_request_avatar(const char *name);
```

- [ ] **Step 1: Write failing app/platform contracts**

Assert JPEG upload sink always selects `JPEG_SLOT_FULL`; JSON avatar changes validate `avatar_store_has` before `save_profile`; invalid names restore the prior value and notify `CFG_ST_ERR_AVATAR`; boot and fullscreen teardown call `jpeg_view_request_avatar(current_name)`; v2 completion maps each field to the same persistence/UI/time paths; all LVGL work remains behind `capture_ui_blocked()`.

- [ ] **Step 2: Verify RED**

Run the app contract tests and focused JPEG tests; expect failures on current avatar-slot behavior.

- [ ] **Step 3: Refactor image source selection**

Keep `jpeg_store` for `imgstore` fullscreen data only. In `jpeg_view`, source avatar bytes from `avatar_store_get(saved_name)` and fullscreen bytes from `jpeg_store_mmap`; retain the same PNG decoder and `imgframe` lifecycle. Store a bounded copy of the requested avatar name until the LVGL timer consumes it.

- [ ] **Step 4: Apply BLE and broadcast fields**

Initialize `avatar_store` before requesting the boot avatar. For JSON, snapshot the previous avatar name, apply JSON, validate a changed name, restore it on failure, and do not clear the current image. Initialize/profile-feed v2 broadcasts beside v1 and apply completed values silently. Save persistent fields before UI refresh; `time` only calls `settimeofday`. During capture, update persisted model state but skip blocking LVGL work.

- [ ] **Step 5: Verify and commit**

Run app contracts, JPEG/PNG/service tests and `idf.py build`. Commit as `feat(app): apply named avatar profile updates`.

---

### Task 7: Remove avatar upload from the host and document both protocols

**Files:**
- Modify: `tools/ble_card_client.py`
- Modify: `tools/test_screen_capture_protocol.py`
- Modify: `doc/ble-protocol-spec.md`
- Modify: `doc/ARCHITECTURE.md`

**Interfaces:**
- Host JSON example includes `nickname` and `avatar_name`.
- Image page sends fullscreen baseline JPEG only.

- [ ] **Step 1: Write failing host tests**

Assert there is no avatar converter, avatar/fullscreen radio selector or avatar file-upload path; the image worker always sends `{"img_mode":"fullscreen"}`; JSON can send `avatar_name`; screenshot controls and fullscreen conversion remain unchanged.

- [ ] **Step 2: Verify RED**

Run `python3 -m unittest tools/test_screen_capture_protocol.py -v`; expect avatar-removal assertions to fail.

- [ ] **Step 3: Simplify the host UI**

Remove `convert_to_png_avatar`, the avatar mode radio button and conditional conversion. Rename the page/help text to fullscreen upload, keep quality controls enabled, and leave screen capture actions unchanged. Add an avatar-name combobox populated with the ten committed names that sends JSON `{"avatar_name":"..."}`.

- [ ] **Step 4: Update documentation**

Document the AVA1 pack, build/release inclusion, BLE JSON fields, fullscreen-only image transfer, exact v2 packet layout, field IDs, HMAC coverage, fragmentation, timeout, replay behavior, error handling and v1 compatibility.

- [ ] **Step 5: Full verification and commit**

Run:

```bash
python3 -m unittest tools/test_normalize_avatar.py tools/test_avatar_pack.py tools/test_screen_capture_protocol.py -v
python3 -m unittest components/app/test/test_screen_capture_contract.py components/app/test/test_avatar_profile_contract.py -v
source /home/cjiio/.espressif/v5.5.3/esp-idf/export.sh
idf.py build
python3 scripts/release.py
git diff --check
```

Run focused Unity suites for profile, JSON, avatar pack, v1 token, v2 profile broadcast, matcher, JPEG and PNG. Inspect firmware size and verify `AVA1` at the `imgava` offset in both `build/imgava.bin` and the merged release image. Commit as `docs: document named avatar profile broadcasts`.

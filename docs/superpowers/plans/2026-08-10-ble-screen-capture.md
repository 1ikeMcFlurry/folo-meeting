# BLE Full-Screen Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the connected BLE host request the device's current 240×320 screen, receive a reliable RGB565 stream, and save the validated image as a user-selected JPG.

**Architecture:** A pure `screen_capture` service owns control parsing, stop-and-wait sequencing, bounds checks, coverage accounting, and CRC32. The ESP32 platform exposes a second GATT Notify characteristic and intercepts LVGL `LV_EVENT_FLUSH_START` during one forced full redraw; the Python host ACKs every outgoing packet and converts the completed RGB565 canvas with Pillow.

**Tech Stack:** C99, ESP-IDF 5.5.2, NimBLE GATT, LVGL 9, FreeRTOS, Unity host tests, Python 3, Bleak, Tkinter, Pillow, `unittest`.

## Global Constraints

- Screen data is 240×320 little-endian RGB565: exactly 153,600 bytes.
- Do not allocate a full-frame buffer on the ESP32-C3 and do not read ST7789 RAM; the board has no PSRAM and LCD MISO is not connected.
- Capture the current LVGL screen, including home, game, full-screen image, and factory/self-test screens.
- Use application message type `0x06`, status characteristic `…0011`, and new screenshot Notify characteristic `…0014`.
- Use stop-and-wait ACKs, a 3-second ACK timeout, and at most three retransmissions per packet.
- Release the LVGL flush buffer and restore normal input/rendering on completion, cancellation, timeout, disconnect, or error.
- Device output remains raw RGB565. Only the host converts to JPG, at quality 90, after full coverage and CRC32 validation.
- Preserve existing JSON, image, score, audio, identity, and game-score behavior for old clients.
- Do not include or overwrite the user's unrelated modification in `docs/superpowers/plans/2026-08-06-trae-card-bsp-demo.md`.

---

## File Map

- Create `components/core/services/include/services/screen_capture.h`: protocol constants, packet structures, and pure state-machine API.
- Create `components/core/services/src/screen_capture.c`: command parsing, packet construction, retry state, region traversal, coverage, and CRC32.
- Create `components/core/services/test/test_screen_capture.c`: Unity tests for all pure protocol/state behavior.
- Modify `components/core/services/CMakeLists.txt`, `components/core/services/test/CMakeLists.txt`, `host_test/main/CMakeLists.txt`: compile the new service and tests.
- Modify `components/core/ports/include/hal/hal_config.h`: screenshot Notify, subscription query, disconnect callback, and type/status constants.
- Modify `components/platform/platform_esp32/src/ble_config.c`: GATT `…0014`, safe outbound Notify API, subscription/disconnect handling.
- Create `components/platform/platform_esp32/include/platform/platform_screen_capture.h`: platform capture start/cancel API.
- Create `components/platform/platform_esp32/src/screen_capture_lvgl.c`: `LV_EVENT_FLUSH_START` interception and FreeRTOS coordination.
- Modify `components/platform/platform_esp32/src/lvgl_port_setup.c`, `components/platform/platform_esp32/CMakeLists.txt`: install the capture hook.
- Modify `components/app/src/app.c`: route type `0x06`, coordinate BLE and display, and guarantee cleanup.
- Create `tools/screen_capture_protocol.py`: host packet parser, reassembler, CRC, and RGB conversion.
- Create `tools/test_screen_capture_protocol.py`: Python unit tests without BLE hardware.
- Modify `tools/ble_card_client.py`: subscribe, ACK, progress/cancel UI, save dialog, and JPG output.
- Modify `doc/ble-protocol-spec.md`: publish protocol v14.

---

### Task 1: Pure Screenshot Protocol and State Machine

**Files:**
- Create: `components/core/services/include/services/screen_capture.h`
- Create: `components/core/services/src/screen_capture.c`
- Create: `components/core/services/test/test_screen_capture.c`
- Modify: `components/core/services/CMakeLists.txt`
- Modify: `components/core/services/test/CMakeLists.txt`
- Modify: `host_test/main/CMakeLists.txt`

**Interfaces:**
- Consumes: integer types and byte buffers only; no LVGL, NimBLE, FreeRTOS, or heap dependency.
- Produces: `screen_capture_init`, `screen_capture_start`, `screen_capture_accept_ack`, `screen_capture_begin_region`, `screen_capture_next_packet`, `screen_capture_timeout`, `screen_capture_cancel`, and `screen_capture_finish`.

- [ ] **Step 1: Write failing protocol tests**

Add Unity cases that assert exact control parsing and META/DATA/END bytes:

```c
TEST_CASE("screen capture parses control commands", "[screen_capture]") {
    sc_control_t c;
    TEST_ASSERT_EQUAL(SC_PARSE_OK, screen_capture_parse_control((uint8_t[]){SC_OP_START}, 1, &c));
    TEST_ASSERT_EQUAL(SC_OP_START, c.op);
    TEST_ASSERT_EQUAL(SC_PARSE_OK, screen_capture_parse_control(
        (uint8_t[]){SC_OP_ACK, 0x34, 0x12, 0x02, 0x00}, 5, &c));
    TEST_ASSERT_EQUAL_HEX16(0x1234, c.capture_id);
    TEST_ASSERT_EQUAL_UINT16(2, c.seq);
    TEST_ASSERT_EQUAL(SC_PARSE_BAD_LENGTH,
        screen_capture_parse_control((uint8_t[]){SC_OP_ACK, 1}, 2, &c));
}

TEST_CASE("screen capture emits deterministic meta", "[screen_capture]") {
    screen_capture_t s;
    uint8_t out[32]; size_t n = 0;
    screen_capture_init(&s, 240, 320);
    TEST_ASSERT_TRUE(screen_capture_start(&s, 0x1234));
    TEST_ASSERT_EQUAL(SC_PACKET_READY, screen_capture_next_packet(&s, 244, out, sizeof out, &n));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(
        ((uint8_t[]){SC_PKT_META,0x34,0x12,0x00,0x00,0xF0,0x00,0x40,0x01,
                     SC_FORMAT_RGB565_LE,0x00,0x58,0x02,0x00}), out, 14);
}
```

- [ ] **Step 2: Run the focused host test and verify failure**

Run:

```bash
idf.py --preview set-target linux
idf.py -C host_test build
```

Expected: compilation fails because `services/screen_capture.h` does not exist.

- [ ] **Step 3: Define the public protocol API**

Create the header with fixed constants and explicit results:

```c
#define SC_WIDTH 240u
#define SC_HEIGHT 320u
#define SC_TOTAL_BYTES (SC_WIDTH * SC_HEIGHT * 2u)
#define SC_OP_START  0x01u
#define SC_OP_ACK    0x02u
#define SC_OP_FINISH 0x03u
#define SC_OP_CANCEL 0x04u
#define SC_PKT_META 0x01u
#define SC_PKT_DATA 0x02u
#define SC_PKT_END  0x03u
#define SC_FORMAT_RGB565_LE 0x01u

typedef enum { SC_PARSE_OK, SC_PARSE_BAD_OP, SC_PARSE_BAD_LENGTH } sc_parse_result_t;
typedef enum { SC_PACKET_READY, SC_PACKET_WAIT_ACK, SC_PACKET_COMPLETE, SC_PACKET_ERROR } sc_packet_result_t;
typedef struct { uint8_t op; uint16_t capture_id; uint16_t seq; } sc_control_t;
typedef struct screen_capture_s screen_capture_t;

sc_parse_result_t screen_capture_parse_control(const uint8_t *p, size_t n, sc_control_t *out);
void screen_capture_init(screen_capture_t *s, uint16_t width, uint16_t height);
bool screen_capture_start(screen_capture_t *s, uint16_t capture_id);
bool screen_capture_begin_region(screen_capture_t *s, uint16_t x, uint16_t y,
                                 uint16_t w, uint16_t h, const uint8_t *rgb565);
sc_packet_result_t screen_capture_next_packet(screen_capture_t *s, uint16_t mtu_payload,
                                               uint8_t *out, size_t cap, size_t *out_len);
bool screen_capture_accept_ack(screen_capture_t *s, uint16_t capture_id, uint16_t seq);
bool screen_capture_timeout(screen_capture_t *s); /* true means retransmit, false means abort */
void screen_capture_cancel(screen_capture_t *s);
bool screen_capture_finish(screen_capture_t *s, uint32_t *crc32);
```

Define `screen_capture_t` in the header as fixed-size state only. It may retain a pointer to the active LVGL region while that producer is blocked, but it must not contain `uint8_t frame[SC_TOTAL_BYTES]` or allocate memory.

- [ ] **Step 4: Implement control parsing and packet encoding**

Implement little-endian helpers locally. DATA headers must be exactly 17 bytes before pixel bytes:

```c
out[0] = SC_PKT_DATA;
put_u16(out + 1, s->capture_id);
put_u16(out + 3, s->seq);
put_u16(out + 5, s->region.x);
put_u16(out + 7, s->region.y);
put_u16(out + 9, s->region.w);
put_u16(out + 11, s->region.h);
put_u32(out + 13, s->region_offset);
memcpy(out + 17, s->region_pixels + s->region_offset, pixel_n);
```

Cache the complete current outgoing packet until the matching ACK arrives so a timeout reproduces byte-identical data. Advance `seq` and region offset only in `screen_capture_accept_ack`.

- [ ] **Step 5: Add state, retry, bounds, coverage, and CRC tests**

Cover these exact cases: wrong capture ID, wrong ACK sequence, three timeout retransmissions followed by abort, region `(239,319,1,1)` accepted, `(239,319,2,1)` rejected, odd region byte count rejected, duplicate/overlapping pixels rejected, missing pixels prevent END, and the ASCII vector `"123456789"` returns CRC32 `0xCBF43926` through the exported testable helper `screen_capture_crc32_update`.

Assert END is exactly `kind:u8, capture_id:u16le, seq:u16le, packet_count:u16le, total_len:u32le, crc32:u32le`; META, DATA, and END therefore all use the same ACK identity `(capture_id, seq)`.

- [ ] **Step 6: Implement bounds, row-major coverage, and CRC without a frame buffer**

Because the forced full redraw is expected to cover the screen once, track coverage as a 9,600-byte bitset (`240*320/8`) and update CRC in final row-major order. Enforce regions arriving in row-major, non-overlapping order for CRC streaming; return a capture error if LVGL violates this invariant. This fixed bitset plus the existing 9.6KB LVGL draw buffer stays far below a 153.6KB frame.

- [ ] **Step 7: Run tests and commit**

Run `idf.py -C host_test build && host_test/build/host_test.elf`; expected: all `[screen_capture]` tests pass. Then:

```bash
git add components/core/services host_test/main/CMakeLists.txt
git commit -m "feat(capture): add screenshot protocol state machine"
```

---

### Task 2: BLE Screenshot Transport Port

**Files:**
- Modify: `components/core/ports/include/hal/hal_config.h`
- Modify: `components/platform/platform_esp32/src/ble_config.c`

**Interfaces:**
- Consumes: screenshot packet bytes produced by Task 1.
- Produces: `hal_config_notify_capture`, `hal_config_capture_subscribed`, `hal_config_get_mtu_payload`, and `hal_config_on_disconnect`.

- [ ] **Step 1: Add compile-time interface coverage**

Add a small test block in `test_screen_capture.c` that includes `hal/hal_config.h` and asserts `CFG_MSG_SCREEN_CAPTURE == 0x06`, then build to prove the constant is absent.

- [ ] **Step 2: Extend the HAL**

Add:

```c
typedef void (*cfg_disconnect_cb_t)(void *user);
void (*on_disconnect)(hal_config_t *, cfg_disconnect_cb_t, void *);
bool (*notify_capture)(hal_config_t *, const uint8_t *, size_t);
bool (*capture_subscribed)(hal_config_t *);
uint16_t (*get_mtu_payload)(hal_config_t *);

#define CFG_MSG_SCREEN_CAPTURE 0x06
#define CFG_ST_ERR_NOT_READY   0x1C
```

Provide null-safe inline wrappers. `get_mtu_payload` returns `min(ble_att_mtu(conn)-3, 244)` and returns 20 when disconnected or unnegotiated.

- [ ] **Step 3: Register GATT characteristic `…0014`**

In `ble_config.c`, add `g_capture_uuid`, `s_capture_handle`, and `s_capture_subscribed`; resize `g_chrs` for five characteristics plus sentinel. Register `BLE_GATT_CHR_F_NOTIFY` with the same read-not-permitted access stub and track its CCCD separately in `BLE_GAP_EVENT_SUBSCRIBE`.

- [ ] **Step 4: Implement safe Notify and disconnect delivery**

Implement `cfg_notify_capture` by allocating an mbuf with `ble_hs_mbuf_from_flat` and calling `ble_gatts_notify_custom`. Return false on no connection, no subscription, allocation failure, or NimBLE error. On disconnect, clear both subscription flags, reset frame reassembly, invoke the registered non-blocking disconnect callback, and restart advertising.

- [ ] **Step 5: Build firmware and commit**

Run `idf.py build`; expected: firmware links with the enlarged GATT table. Then:

```bash
git add components/core/ports/include/hal/hal_config.h components/platform/platform_esp32/src/ble_config.c
git commit -m "feat(ble): add screenshot notify transport"
```

---

### Task 3: LVGL Full-Redraw Capture Adapter

**Files:**
- Create: `components/platform/platform_esp32/include/platform/platform_screen_capture.h`
- Create: `components/platform/platform_esp32/src/screen_capture_lvgl.c`
- Modify: `components/platform/platform_esp32/src/lvgl_port_setup.c`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

**Interfaces:**
- Consumes: the active `lv_display_t`, `LV_EVENT_FLUSH_START`, and a region callback supplied by app.
- Produces: `platform_screen_capture_init`, `platform_screen_capture_start`, `platform_screen_capture_cancel`, and a terminal callback.

- [ ] **Step 1: Define the adapter interface and make the build fail**

Create the header declaration and include it from `lvgl_port_setup.c` before implementation:

```c
typedef bool (*platform_capture_region_cb_t)(int x, int y, int w, int h,
                                             const uint8_t *rgb565, void *user);
typedef void (*platform_capture_done_cb_t)(bool ok, void *user);
void platform_screen_capture_init(lv_display_t *disp,
    platform_capture_region_cb_t region_cb, platform_capture_done_cb_t done_cb, void *user);
bool platform_screen_capture_start(void);
void platform_screen_capture_cancel(void);
bool platform_screen_capture_active(void);
```

Run `idf.py build`; expected: undefined adapter symbols.

- [ ] **Step 2: Implement the flush-start hook**

Register `lv_display_add_event_cb(disp, capture_flush_start, LV_EVENT_FLUSH_START, NULL)`. In the callback, obtain `lv_area_t *area = lv_event_get_param(e)` and `lv_draw_buf_t *buf = lv_display_get_buf_active(disp)`. Pass `buf->data` before `esp_lvgl_port` swaps RGB565 bytes. When active, validate the area, call `region_cb`, and block that LVGL callback until the app has transmitted and ACKed the region; returning from the event then lets the normal LCD flush continue.

- [ ] **Step 3: Force exactly one full redraw and freeze input**

`platform_screen_capture_start` must run under `platform_lvgl_lock`, disable the navigation input with `platform_lvgl_nav_enable(false)`, set active state, invalidate `lv_layer_bottom()` and `lv_screen_active()`, and call `lv_refr_now(disp)`. Do not call `lv_timer_enable(false)`, because that also prevents refreshing. The synchronous `lv_refr_now` plus blocking flush events naturally pauses LVGL animations during transmission.

- [ ] **Step 4: Make cleanup idempotent**

All exits call one internal function that clears active state and re-enables navigation exactly once. `platform_screen_capture_cancel` must wake any semaphore wait so disconnect/cancel cannot strand the LVGL task.

- [ ] **Step 5: Build and commit**

Run `idf.py build`; expected: no LVGL API/type errors. Then:

```bash
git add components/platform/platform_esp32
git commit -m "feat(display): capture forced LVGL redraw regions"
```

---

### Task 4: Application Orchestration and Failure Cleanup

**Files:**
- Modify: `components/app/src/app.c`

**Interfaces:**
- Consumes: Task 1 state machine, Task 2 BLE HAL, and Task 3 LVGL adapter.
- Produces: complete device-side screenshot request/ACK/cancel flow.

- [ ] **Step 1: Add screenshot dispatch with unsupported behavior first**

Extend `on_cfg_message` with `CFG_MSG_SCREEN_CAPTURE`, parse the command, and initially return `CFG_ST_ERR_NOT_READY`. Build once to confirm the new branch compiles before adding concurrency.

- [ ] **Step 2: Add the dedicated transfer task**

Create one task and fixed queue/semaphores during app initialization. The NimBLE callback only parses commands and signals the task. The LVGL region callback supplies a pointer valid until it is released. The transfer task repeatedly calls `screen_capture_next_packet`, sends through `hal_config_notify_capture`, then waits up to 3 seconds for the matching ACK semaphore. It invokes `screen_capture_timeout` and resends up to three times.

- [ ] **Step 3: Wire START, ACK, FINISH, and CANCEL**

START requires screenshot subscription and no active image decode/upload/capture. Allocate a rolling nonzero `capture_id`, return `[0x06][ACK]`, and request platform capture. ACK validates ID/sequence and wakes only the current wait. CANCEL and disconnect invoke both core and platform cleanup. END ACK immediately restores the display and sends `[0x06][DONE]`; FINISH only clears the five-minute lightweight result record.

- [ ] **Step 4: Add timeout and race tests at the pure boundary**

Extend `test_screen_capture.c` to model ACK arriving just before timeout, cancel while waiting, disconnect after META, and stale ACK after a new capture ID. Assert each terminal path leaves `active=false` and rejects further packets from the old ID.

- [ ] **Step 5: Verify heap and build**

Run:

```bash
idf.py -C host_test build && host_test/build/host_test.elf
idf.py build
idf.py size
```

Expected: tests pass; firmware builds; static screenshot allocations are limited to state, a 9,600-byte coverage bitset, queue/semaphores, and one MTU-sized packet—never 153,600 bytes.

- [ ] **Step 6: Commit**

```bash
git add components/app/src/app.c components/core/services/test/test_screen_capture.c
git commit -m "feat(app): orchestrate reliable BLE screenshots"
```

---

### Task 5: Host Protocol Reassembler and RGB Conversion

**Files:**
- Create: `tools/screen_capture_protocol.py`
- Create: `tools/test_screen_capture_protocol.py`

**Interfaces:**
- Consumes: raw notifications from `…0014`.
- Produces: `CaptureAssembler.feed(data) -> Ack | Complete`, `CaptureAssembler.cancel()`, and `rgb565le_to_image(data, width, height) -> PIL.Image.Image`.

- [ ] **Step 1: Write failing Python tests**

Use `unittest` and deterministic 2×2 pixels:

```python
class TestRgb565(unittest.TestCase):
    def test_primary_colors(self):
        raw = struct.pack("<4H", 0xF800, 0x07E0, 0x001F, 0xFFFF)
        im = rgb565le_to_image(raw, 2, 2)
        self.assertEqual(list(im.getdata()), [
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
```

Also test META acceptance, DATA placement by `(x,y,w,h,region_offset)`, duplicate sequence ACK without double-writing, wrong ID, sequence gap, out-of-bounds region, incomplete coverage, and CRC mismatch.

- [ ] **Step 2: Run tests and verify import failure**

Run `python -m unittest tools/test_screen_capture_protocol.py -v`; expected: import failure for `screen_capture_protocol`.

- [ ] **Step 3: Implement strict packet parsing and assembly**

Use `bytearray(width*height*2)` plus a host-only coverage bitmap. Require META dimensions 240×320, format 1, and total length 153,600. DATA validates its fixed 17-byte header, even payload length, region bounds, region offset, and expected sequence. Write each pixel row to its absolute canvas offset. Duplicate of the immediately previous sequence returns the same ACK without applying bytes twice; all other sequence mismatches raise `CaptureProtocolError`.

- [ ] **Step 4: Implement END validation and RGB conversion**

At END, require all coverage bits, exact packet count/length, and `zlib.crc32(canvas) & 0xffffffff == advertised_crc`. Expand channels with bit replication:

```python
r8 = (r5 << 3) | (r5 >> 2)
g8 = (g6 << 2) | (g6 >> 4)
b8 = (b5 << 3) | (b5 >> 2)
```

Return `Image.frombytes("RGB", (width, height), rgb_bytes)` without rotation or mirroring.

- [ ] **Step 5: Run tests and commit**

Run `python -m unittest tools/test_screen_capture_protocol.py -v`; expected: all tests pass. Then:

```bash
git add tools/screen_capture_protocol.py tools/test_screen_capture_protocol.py
git commit -m "feat(tools): add screenshot stream reassembler"
```

---

### Task 6: BLE Client and Save-As UI

**Files:**
- Modify: `tools/ble_card_client.py`
- Modify: `tools/test_screen_capture_protocol.py`

**Interfaces:**
- Consumes: `CaptureAssembler` and `rgb565le_to_image` from Task 5.
- Produces: user-visible screenshot, progress, cancel, and save-as workflow.

- [ ] **Step 1: Add client constants and connection subscription**

Define `CAPTURE_UUID = "54524145-4341-5244-0000-000000000014"` and `TYPE_CAPTURE = 0x06`. Subscribe to both status and capture characteristics in `_connect`; if capture subscription fails, disconnect and surface the exception instead of reporting a partially usable connection.

- [ ] **Step 2: Implement asynchronous screenshot control**

Add `_start_capture`, `_ack_capture`, `_finish_capture`, and `_cancel_capture`, all using existing `_write_frame(TYPE_CAPTURE, payload)`. The capture notification callback feeds `CaptureAssembler`, posts progress through `on_event`, and schedules ACK writes on the Bleak loop. Never call Tk or Pillow file dialogs from this callback.

- [ ] **Step 3: Add GUI controls and thread-safe events**

Add a “截图” notebook page with “截图并保存”, “取消”, progress bar, and status label. Disable start while disconnected or active; enable cancel only while active. On the `capture_complete` event, convert to a Pillow image in the GUI thread and call:

```python
path = filedialog.asksaveasfilename(
    defaultextension=".jpg",
    initialfile=time.strftime("trae-card-%Y%m%d-%H%M%S.jpg"),
    filetypes=[("JPEG 图片", "*.jpg *.jpeg")])
```

If a path is selected, save with `image.save(path, "JPEG", quality=90)` and send FINISH. If it is canceled or saving raises, send CANCEL and do not create/retain a partial output file.

- [ ] **Step 4: Handle error and disconnect cleanup**

Protocol error, screenshot terminal error, cancel, or disconnect clears the assembler and restores buttons. During capture disable image upload and other bulk-transfer buttons; restore their prior enabled state afterward. Log transferred bytes and elapsed seconds.

- [ ] **Step 5: Add source-level GUI contract tests**

Extend the Python tests with a fake event sink and fake worker to assert that completion produces a `capture_complete` event, cancellation sends `[SC_OP_CANCEL, id_lo, id_hi]`, and disconnect clears active assembly. Keep dialogs out of automated tests by extracting `_save_capture_image(image, dialog=filedialog)` and inject a fake dialog.

- [ ] **Step 6: Run tests and smoke-check import**

Run:

```bash
python -m unittest tools/test_screen_capture_protocol.py -v
python -m py_compile tools/ble_card_client.py tools/screen_capture_protocol.py
```

Expected: tests pass and both modules compile.

- [ ] **Step 7: Commit**

```bash
git add tools/ble_card_client.py tools/test_screen_capture_protocol.py
git commit -m "feat(tools): save BLE screen captures as JPG"
```

---

### Task 7: Protocol Documentation and End-to-End Verification

**Files:**
- Modify: `doc/ble-protocol-spec.md`
- Modify: `README.md` only if it contains the current upper-computer feature list.

**Interfaces:**
- Consumes: final constants and behavior from Tasks 1–6.
- Produces: protocol v14 integration contract and verified release candidate.

- [ ] **Step 1: Update the protocol document**

Bump the document to v14. Add `type=0x06`, characteristic `…0014`, START/ACK/FINISH/CANCEL payloads, META/DATA/END layouts, little-endian rules, RGB565 format, CRC ordering, 3-second/three-retry behavior, busy/not-ready statuses, 15–40 second estimate, cancellation, and backward compatibility.

- [ ] **Step 2: Run the complete automated verification**

Run:

```bash
idf.py -C host_test build && host_test/build/host_test.elf
python -m unittest tools/test_screen_capture_protocol.py -v
python -m py_compile tools/ble_card_client.py tools/screen_capture_protocol.py
idf.py build
git diff --check
```

Expected: Unity and Python tests pass, Python compiles, firmware builds, and `git diff --check` emits no errors.

- [ ] **Step 3: Perform hardware acceptance**

Flash the firmware and use `tools/ble_card_client.py` to save four JPGs: home, game, full-screen JPEG, and factory/self-test solid color. Confirm each is exactly 240×320, upright, not mirrored, with correct red/green/blue channels, black rounded corners, and readable text. Cancel one capture, disconnect during one capture, and force one ACK timeout; confirm input and animation recover every time. Finally upload a full-screen JPEG to confirm the existing bulk-transfer path still works.

- [ ] **Step 4: Record hardware evidence**

Append a short “实机验证” subsection to `doc/ble-protocol-spec.md` containing firmware commit, host OS, negotiated MTU, elapsed time for each screen, and pass/fail for cancel/disconnect/timeout recovery. If hardware is unavailable, do not claim this step passed; report it as the sole remaining manual verification.

- [ ] **Step 5: Commit documentation**

```bash
git add doc/ble-protocol-spec.md README.md
git commit -m "docs: document BLE screen capture protocol v14"
```

Only add `README.md` if it actually changed.

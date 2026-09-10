# Game Obstacle Wall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace keyword-text obstacles in the three-lane runner with fixed orange rounded rectangular walls whose rendered bounds and collision bounds agree.

**Architecture:** Keep the existing obstacle pool and game loop, but construct each pooled obstacle as a plain LVGL rectangle. Define wall dimensions beside the other game constants and use those dimensions consistently for creation, positioning, collision, scoring, and recycling.

**Tech Stack:** C11, LVGL 9.5, Python 3 `unittest`, ESP-IDF 5.5.3.

## Global Constraints

- Each obstacle is a plain rectangle measuring exactly `14×72px`.
- Each wall uses a dedicated `COL_OBST` constant set to `lv_color_hex(0xF59E0B)`.
- Walls contain no text or border and use a `5px` corner radius.
- Preserve the five-object pool, three-lane random spawning, `120px` spawn gap, speed progression, scoring, dash invulnerability, cooldown, controls, persistence, and BLE behavior.
- Collision, scoring, and recycling must use the wall's fixed `14px` width.
- Do not stage or modify unrelated generated `sdkconfig` changes.

---

### Task 1: Replace keyword obstacles with rectangular walls

**Files:**
- Create: `components/ui/presentation/test/test_game_obstacle_wall.py`
- Modify: `components/ui/presentation/src/ui_game.c`

**Interfaces:**
- Consumes: existing `make_rect()`, `obst_t`, `spawn_maybe()`, and `game_tick()` behavior.
- Produces: pooled obstacles constructed with `make_rect(G.scr, OBST_W, OBST_H, COL_OBST, OBST_RADIUS)` and fixed width `OBST_W` for game-loop bounds.

- [ ] **Step 1: Write the failing source-contract test**

Create `components/ui/presentation/test/test_game_obstacle_wall.py`:

```python
"""Source contracts for the runner's rectangular wall obstacles."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[4]
GAME_SOURCE = ROOT / "components/ui/presentation/src/ui_game.c"


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.DOTALL)
    if match is None:
        raise AssertionError(f"function {name} is missing")
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    raise AssertionError(f"function {name} has no closing brace")


class GameObstacleWallContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = GAME_SOURCE.read_text(encoding="utf-8")

    def test_wall_constants_define_committed_size(self) -> None:
        self.assertRegex(self.source, r"#define\s+OBST_W\s+14\b")
        self.assertRegex(self.source, r"#define\s+OBST_H\s+72\b")
        self.assertRegex(self.source, r"#define\s+OBST_RADIUS\s+5\b")
        self.assertRegex(
            self.source,
            r"#define\s+COL_OBST\s+lv_color_hex\(0xF59E0B\)",
        )

    def test_obstacle_pool_uses_plain_rounded_orange_rectangles(self) -> None:
        scene = function_body(self.source, "build_scene")
        self.assertIn(
            "make_rect(G.scr, OBST_W, OBST_H, COL_OBST, OBST_RADIUS)", scene
        )
        obstacle_block = scene[scene.find("for (int i = 0; i < N_OBST; i++)"):]
        self.assertNotIn("lv_label_create", obstacle_block.split("// 玩家", 1)[0])

    def test_spawn_uses_fixed_wall_bounds_and_no_keyword_text(self) -> None:
        spawn = function_body(self.source, "spawn_maybe")
        self.assertIn("b->w = OBST_W;", spawn)
        self.assertIn("lane_center(b->lane) - OBST_H / 2", spawn)
        self.assertNotIn("lv_label_set_text", spawn)
        self.assertNotIn("CODE_KW", self.source)
        self.assertNotIn("CODE_COL", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```bash
python3 -m unittest components/ui/presentation/test/test_game_obstacle_wall.py -v
```

Expected: failures because the committed height, orange color, corner radius, rectangle construction, and fixed spawn bounds do not exist yet.

- [ ] **Step 3: Implement the minimal wall obstacle**

In `ui_game.c`, add beside `N_OBST`:

```c
#define OBST_W   14
#define OBST_H   72
#define OBST_RADIUS 5
```

Add beside the palette constants:

```c
#define COL_OBST    lv_color_hex(0xF59E0B)   // 障碍墙橙色
```

Delete `CODE_KW`, `CODE_COL`, `CODE_KW_N`, and `CODE_COL_N`. In `spawn_maybe()`, replace label text/color/layout measurement with:

```c
    b->w = OBST_W;
    lv_obj_set_pos(b->o, b->x, lane_center(b->lane) - OBST_H / 2);
```

In the obstacle-pool loop in `build_scene()`, replace label construction with:

```c
        lv_obj_t *o = make_rect(G.scr, OBST_W, OBST_H, COL_OBST, OBST_RADIUS);
        lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
        G.ob[i].o = o;
        G.ob[i].active = false;
```

Update nearby comments so they describe fixed orange rounded rectangular walls instead of code keywords.

- [ ] **Step 4: Verify GREEN and firmware integration**

Run:

```bash
python3 -m unittest components/ui/presentation/test/test_game_obstacle_wall.py -v
source /home/cjiio/.espressif/v5.5.3/esp-idf/export.sh
idf.py build
git diff --check
```

Expected: 3/3 contract tests pass, firmware build completes, and the diff check reports no whitespace errors.

- [ ] **Step 5: Commit the implementation**

```bash
git add \
  components/ui/presentation/test/test_game_obstacle_wall.py \
  components/ui/presentation/src/ui_game.c
git commit -m "feat(game): replace keyword obstacles with walls"
```

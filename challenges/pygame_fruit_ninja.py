"""
Pygame Fruit Ninja game process.

Runs in a separate process from the Hailo pose estimation pipeline.
Receives hand positions via queue, sends fruit positions back for video overlay.
"""
import json
import math
import queue
import random
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import pygame

SCORES_FILE = Path(__file__).parent / "scores.json"
MAX_HIGH_SCORES = 10
ROUND_DURATION = 60       # seconds per round
COUNTDOWN_SECONDS = 3     # 3-2-1 before GO!
HANDS_TO_START_SECS = 1.5 # how long hands must be visible to auto-start


class GameState(Enum):
    ATTRACT = auto()    # idle, showing leaderboard
    COUNTDOWN = auto()  # 3-2-1-GO!
    PLAYING = auto()    # active round
    GAME_OVER = auto()  # results screen


class FruitType(Enum):
    APPLE = "apple"
    ORANGE = "orange"
    BANANA = "banana"
    WATERMELON = "watermelon"
    STRAWBERRY = "strawberry"
    BOMB = "bomb"


@dataclass
class Fruit:
    x: float
    y: float
    vx: float
    vy: float
    fruit_type: FruitType
    size: int
    sliced: bool = False
    creation_time: float = 0.0
    exploding: bool = False
    explosion_start_frame: int | None = None
    _original_size: int = 0
    class_id: int = 0


class PygameFruitNinja:
    # ── Medium difficulty base params ────────────────────────────────────────
    GRAVITY_BASE = 0.12
    SPAWN_RATE_BASE = 0.020
    SLICE_DISTANCE = 45
    FRUIT_LIFETIME = 9.0
    FRUIT_VX_RANGE = (-2.0, 2.0)
    FRUIT_VY_RANGE = (-14, -7)
    FRUIT_SIZE_RANGE = (40, 65)

    # Bombs
    BOMB_CHANCE = 0.12          # 12 % of spawns
    BOMB_PENALTY = 20           # points deducted
    BOMB_FLASH_DURATION = 0.4   # seconds of red flash

    # Difficulty ramp: (elapsed_secs, spawn_rate, gravity)
    DIFFICULTY_STEPS = [
        (20, 0.035, 0.16),
        (40, 0.055, 0.22),
    ]

    # Explosion animation
    EXPLOSION_FRAMES = 15
    EXPLOSION_GROWTH_PER_FRAME = 1.04

    # Fruit visual properties
    FRUIT_COLORS = {
        FruitType.APPLE:      (220,  50,  50),
        FruitType.ORANGE:     (255, 165,   0),
        FruitType.BANANA:     (255, 240,  50),
        FruitType.WATERMELON: ( 50, 210,  80),
        FruitType.STRAWBERRY: (255,  50, 120),
        FruitType.BOMB:       ( 30,  30,  30),
    }
    FRUIT_CLASS_IDS = {
        FruitType.APPLE: 1, FruitType.ORANGE: 6, FruitType.BANANA: 3,
        FruitType.WATERMELON: 9, FruitType.STRAWBERRY: 0, FruitType.BOMB: 2,
    }
    SLICEABLE_FRUITS = [
        FruitType.APPLE, FruitType.ORANGE, FruitType.BANANA,
        FruitType.WATERMELON, FruitType.STRAWBERRY,
    ]

    # UI palette
    C_WHITE  = (255, 255, 255)
    C_YELLOW = (255, 230,  50)
    C_RED    = (220,  50,  50)
    C_CYAN   = (  0, 220, 255)
    C_ORANGE = (255, 140,   0)
    C_GREEN  = ( 80, 220,  80)
    C_BG     = ( 10,  10,  20)
    C_BLACK  = (  0,   0,   0)

    # ── Init ────────────────────────────────────────────────────────────────

    def __init__(self, hand_positions_queue, fruits_queue, snapshot_queue,
                 frame_width: int, frame_height: int,
                 result_file: Path | None = None) -> None:
        self.hand_positions_queue = hand_positions_queue
        self.fruits_queue = fruits_queue
        self.snapshot_queue = snapshot_queue
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.result_file = result_file

        pygame.init()
        self.screen = pygame.display.set_mode((frame_width, frame_height))
        pygame.display.set_caption("Fruit Ninja")
        self.clock = pygame.time.Clock()

        self.font_huge   = pygame.font.Font(None, 200)
        self.font_large  = pygame.font.Font(None,  80)
        self.font_medium = pygame.font.Font(None,  52)
        self.font_small  = pygame.font.Font(None,  36)

        # Game state
        self.state: GameState = GameState.ATTRACT
        self.fruits: list[Fruit] = []
        self.hand_positions: dict[int, tuple[int, int]] = {}
        self.score = 0
        self.running = True
        self.frame_count = 0

        # Timers
        self.round_start_time = 0.0
        self.countdown_start_time = 0.0
        self.game_over_time = 0.0
        self.bomb_flash_until = 0.0
        self.hands_detected_since = 0.0

        # Difficulty
        self.current_gravity = self.GRAVITY_BASE
        self.current_spawn_rate = self.SPAWN_RATE_BASE

        self.high_scores: list[int] = self._load_scores()
        print(f"Fruit Ninja initialized ({frame_width}x{frame_height})")

    # ── Score persistence ────────────────────────────────────────────────────

    def _load_scores(self) -> list[int]:
        try:
            if SCORES_FILE.exists():
                data = json.loads(SCORES_FILE.read_text())
                return sorted(data.get("scores", []), reverse=True)[:MAX_HIGH_SCORES]
        except Exception:
            pass
        return []

    def _save_score(self, score: int) -> None:
        self.high_scores.append(score)
        self.high_scores = sorted(self.high_scores, reverse=True)[:MAX_HIGH_SCORES]
        try:
            SCORES_FILE.write_text(json.dumps({"scores": self.high_scores}))
        except Exception as e:
            print(f"Failed to save scores: {e}")

    # ── Difficulty ramp ──────────────────────────────────────────────────────

    def _update_difficulty(self) -> None:
        elapsed = time.time() - self.round_start_time
        spawn_rate = self.SPAWN_RATE_BASE
        gravity = self.GRAVITY_BASE
        for threshold, sr, gv in self.DIFFICULTY_STEPS:
            if elapsed >= threshold:
                spawn_rate, gravity = sr, gv
        self.current_spawn_rate = spawn_rate
        self.current_gravity = gravity

    # ── Fruit logic ──────────────────────────────────────────────────────────

    def _spawn_fruit(self) -> None:
        x = random.randint(60, self.frame_width - 60)
        y = self.frame_height + 50
        vx = random.uniform(*self.FRUIT_VX_RANGE)
        vy = random.uniform(*self.FRUIT_VY_RANGE)
        fruit_type = (FruitType.BOMB if random.random() < self.BOMB_CHANCE
                      else random.choice(self.SLICEABLE_FRUITS))
        size = random.randint(*self.FRUIT_SIZE_RANGE)
        self.fruits.append(Fruit(
            x=x, y=y, vx=vx, vy=vy,
            fruit_type=fruit_type,
            size=size,
            _original_size=size,
            creation_time=time.time(),
            class_id=self.FRUIT_CLASS_IDS[fruit_type],
        ))

    def _update_fruits(self) -> None:
        now = time.time()
        to_remove: list[Fruit] = []

        for fruit in self.fruits:
            if fruit.exploding:
                if fruit.explosion_start_frame is None:
                    fruit.explosion_start_frame = self.frame_count
                frames_elapsed = self.frame_count - fruit.explosion_start_frame
                fruit.size = int(fruit._original_size
                                 * (self.EXPLOSION_GROWTH_PER_FRAME ** frames_elapsed))
                if frames_elapsed >= self.EXPLOSION_FRAMES:
                    to_remove.append(fruit)
                continue

            fruit.vy += self.current_gravity
            fruit.x += fruit.vx
            fruit.y += fruit.vy

            if (fruit.y > self.frame_height + 100
                    or now - fruit.creation_time > self.FRUIT_LIFETIME):
                to_remove.append(fruit)

        for f in to_remove:
            self.fruits.remove(f)

        self.frame_count += 1

    def _check_slicing(self) -> None:
        for _hand_id, (hx, hy) in self.hand_positions.items():
            for fruit in self.fruits:
                if fruit.sliced:
                    continue
                dist = math.sqrt((hx - fruit.x) ** 2 + (hy - fruit.y) ** 2)
                if dist < self.SLICE_DISTANCE:
                    fruit.sliced = True
                    fruit.exploding = True
                    if fruit.fruit_type == FruitType.BOMB:
                        self.score = max(0, self.score - self.BOMB_PENALTY)
                        self.bomb_flash_until = time.time() + self.BOMB_FLASH_DURATION
                        print(f"BOMB! Score: {self.score}")
                    else:
                        self.score += 10
                        print(f"Sliced! Score: {self.score}")

    def _send_fruit_positions(self) -> None:
        try:
            for fruit in self.fruits:
                self.fruits_queue.put_nowait({
                    'type': fruit.fruit_type.value,
                    'position': (int(fruit.x), int(fruit.y)),
                    'size': fruit.size,
                    'sliced': fruit.sliced,
                    'exploding': fruit.exploding,
                    'class_id': fruit.class_id,
                })
        except queue.Full:
            pass

    def _receive_hand_positions(self) -> None:
        try:
            while True:
                self.hand_positions = self.hand_positions_queue.get_nowait()
        except queue.Empty:
            pass

    # ── Drawing helpers ──────────────────────────────────────────────────────

    def _text_centered(self, text: str, font, color, y: int,
                       shadow: bool = True) -> None:
        if shadow:
            s = font.render(text, True, self.C_BLACK)
            r = s.get_rect(centerx=self.frame_width // 2, y=y + 3)
            self.screen.blit(s, r)
        s = font.render(text, True, color)
        r = s.get_rect(centerx=self.frame_width // 2, y=y)
        self.screen.blit(s, r)

    def _draw_fruits(self) -> None:
        for fruit in self.fruits:
            color = self.FRUIT_COLORS[fruit.fruit_type]
            cx, cy = int(fruit.x), int(fruit.y)
            r = fruit.size

            if fruit.exploding:
                exp_color = (100, 30, 30) if fruit.fruit_type == FruitType.BOMB else (255, 255, 128)
                pygame.draw.circle(self.screen, exp_color, (cx, cy), r, width=4)
                continue

            if fruit.sliced:
                surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
                pygame.draw.circle(surf, (*color, 128), (r, r), r)
                self.screen.blit(surf, (cx - r, cy - r))
            else:
                pygame.draw.circle(self.screen, color, (cx, cy), r)
                if fruit.fruit_type == FruitType.BOMB:
                    half = r // 2
                    pygame.draw.line(self.screen, (200, 200, 200),
                                     (cx - half, cy - half), (cx + half, cy + half), 3)
                    pygame.draw.line(self.screen, (200, 200, 200),
                                     (cx + half, cy - half), (cx - half, cy + half), 3)

    def _draw_hands(self) -> None:
        for _hand_id, (hx, hy) in self.hand_positions.items():
            pygame.draw.circle(self.screen, self.C_WHITE, (hx, hy), 12)
            pygame.draw.circle(self.screen, self.C_CYAN,  (hx, hy),  7)

    def _draw_hud(self, time_left: float) -> None:
        score_surf = self.font_medium.render(f"Score: {self.score}", True, self.C_WHITE)
        self.screen.blit(score_surf, (15, 15))

        # Timer bar
        bar_w, bar_h = 300, 22
        bar_x = self.frame_width - bar_w - 15
        bar_y = 20
        ratio = max(0.0, time_left / ROUND_DURATION)
        bar_color = (self.C_GREEN if ratio > 0.4 else
                     self.C_ORANGE if ratio > 0.15 else self.C_RED)
        pygame.draw.rect(self.screen, (60, 60, 60),
                         (bar_x, bar_y, bar_w, bar_h), border_radius=6)
        if ratio > 0:
            pygame.draw.rect(self.screen, bar_color,
                             (bar_x, bar_y, int(bar_w * ratio), bar_h), border_radius=6)
        pygame.draw.rect(self.screen, self.C_WHITE,
                         (bar_x, bar_y, bar_w, bar_h), 2, border_radius=6)

        timer_surf = self.font_small.render(f"{int(time_left)}s", True, self.C_WHITE)
        self.screen.blit(timer_surf, (bar_x + bar_w + 8, bar_y))

        # Bomb warning
        bomb_hint = self.font_small.render("Avoid bombs (-20pts)!", True, (180, 60, 60))
        self.screen.blit(bomb_hint, (15, self.frame_height - 40))

    def _draw_leaderboard(self, y_start: int) -> None:
        self._text_centered("HIGH SCORES", self.font_large, self.C_YELLOW, y_start)
        if not self.high_scores:
            self._text_centered("No scores yet — be the first!",
                                self.font_small, self.C_WHITE, y_start + 90)
            return
        rank_colors = [self.C_YELLOW, (200, 200, 200), (180, 120, 60)]
        for i, s in enumerate(self.high_scores[:MAX_HIGH_SCORES]):
            rank = f"#{i + 1}"
            color = rank_colors[i] if i < 3 else self.C_WHITE
            line = f"  {rank:<4}  {s:>6} pts"
            self._text_centered(line, self.font_small, color, y_start + 80 + i * 38)

    # ── State screens ────────────────────────────────────────────────────────

    def _draw_attract(self) -> None:
        self.screen.fill(self.C_BG)
        self._text_centered("FRUIT NINJA", self.font_large,
                            self.C_YELLOW, 40)
        self._text_centered("Step up and wave both hands to start!",
                            self.font_small, self.C_WHITE, 130)
        self._draw_leaderboard(190)

    def _draw_countdown(self) -> None:
        self.screen.fill(self.C_BG)
        elapsed = time.time() - self.countdown_start_time
        num = COUNTDOWN_SECONDS - int(elapsed)
        if num > 0:
            self._text_centered(str(num), self.font_huge, self.C_YELLOW,
                                self.frame_height // 2 - 100)
        else:
            self._text_centered("GO!", self.font_huge, self.C_GREEN,
                                self.frame_height // 2 - 100)

    def _draw_playing(self, time_left: float) -> None:
        if time.time() < self.bomb_flash_until:
            self.screen.fill((80, 0, 0))
        else:
            self.screen.fill(self.C_BLACK)
        self._draw_fruits()
        self._draw_hands()
        self._draw_hud(time_left)

    def _draw_game_over(self) -> None:
        self.screen.fill(self.C_BG)
        self._text_centered("TIME'S UP!", self.font_large,
                            self.C_RED, 40)
        self._text_centered(f"Your Score:  {self.score} pts",
                            self.font_medium, self.C_WHITE, 130)

        rank = sum(1 for s in self.high_scores if s > self.score) + 1
        is_top3 = rank <= 3 and self.score > 0
        rank_text = (f"Rank #{rank}  — NEW HIGH SCORE!" if is_top3
                     else f"Rank #{rank}")
        self._text_centered(rank_text, self.font_small,
                            self.C_YELLOW if is_top3 else self.C_WHITE, 200)
        self._draw_leaderboard(260)

    # ── State transitions ────────────────────────────────────────────────────

    def _start_countdown(self) -> None:
        self.state = GameState.COUNTDOWN
        self.countdown_start_time = time.time()
        self.fruits = []

    def _start_playing(self) -> None:
        self.state = GameState.PLAYING
        self.fruits = []
        self.score = 0
        self.frame_count = 0
        self.round_start_time = time.time()
        self.current_gravity = self.GRAVITY_BASE
        self.current_spawn_rate = self.SPAWN_RATE_BASE

    def _end_game(self) -> None:
        self.state = GameState.GAME_OVER
        self.game_over_time = time.time()
        self.fruits = []
        self._save_score(self.score)
        try:
            self.snapshot_queue.put_nowait({'score': self.score})
        except Exception:
            pass
        print(f"Round ended. Score: {self.score}")
        if self.result_file is not None:
            try:
                self.result_file.write_text(json.dumps({'score': self.score}))
                print(f"Score written to {self.result_file}")
            except Exception as e:
                print(f"Failed to write result file: {e}")

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self) -> None:
        print("Starting game loop...")
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.running = False
                    elif event.key == pygame.K_SPACE and self.state == GameState.ATTRACT:
                        self._start_countdown()

            self._receive_hand_positions()
            now = time.time()

            if self.state == GameState.ATTRACT:
                if self.hand_positions:
                    if self.hands_detected_since == 0.0:
                        self.hands_detected_since = now
                    elif now - self.hands_detected_since >= HANDS_TO_START_SECS:
                        self.hands_detected_since = 0.0
                        self._start_countdown()
                else:
                    self.hands_detected_since = 0.0
                self._draw_attract()

            elif self.state == GameState.COUNTDOWN:
                elapsed = now - self.countdown_start_time
                if elapsed >= COUNTDOWN_SECONDS + 0.8:
                    self._start_playing()
                self._draw_countdown()

            elif self.state == GameState.PLAYING:
                time_left = ROUND_DURATION - (now - self.round_start_time)
                if time_left <= 0:
                    self._end_game()
                else:
                    self._update_difficulty()
                    if random.random() < self.current_spawn_rate:
                        self._spawn_fruit()
                    self._update_fruits()
                    self._check_slicing()
                    self._send_fruit_positions()
                    self._draw_playing(time_left)

            elif self.state == GameState.GAME_OVER:
                self._draw_game_over()
                if now - self.game_over_time >= 7.0:
                    if self.result_file is not None:
                        # CTF mode: exit after one round so the runner can collect result
                        self.running = False
                    else:
                        self.state = GameState.ATTRACT

            pygame.display.flip()
            self.clock.tick(60)

        print("Game loop ended.")
        pygame.quit()

    @staticmethod
    def run_game(hand_positions_queue, fruits_queue, snapshot_queue,
                 frame_width: int, frame_height: int,
                 result_file: Path | None = None) -> None:
        """Entry point for multiprocessing.Process."""
        try:
            game = PygameFruitNinja(
                hand_positions_queue, fruits_queue, snapshot_queue,
                frame_width, frame_height,
                result_file=result_file,
            )
            game.run()
        except Exception as e:
            import traceback
            print(f"Error in pygame process: {e}")
            traceback.print_exc()
        finally:
            try:
                pygame.quit()
            except Exception:
                pass

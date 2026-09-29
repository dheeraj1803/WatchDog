# Watchdog: Build Plan (Congressional App Challenge 2026)

_Written 2026-09-29. Owner: Dheeraj. AI role: mentor and boilerplate only (see CLAUDE.md)._

## 0. Ground rules for this plan

- **[AI-SCAFFOLD]**: Claude may write it (boilerplate under CLAUDE.md). I still read it and must be able to explain it.
- **[MINE]**: I write it. Claude explains the approach, gives pseudocode, and reviews my code.
- **[BOUNDARY]**: not clearly one or the other. The recommendation is listed, and **Claude asks before writing any of it**.
- Before a module counts as done: an **explain-back check** (I walk Claude through my code) and a row in `AI_USAGE.md` for the session.
- Rule from the start: **fail toward alerting.** If anything breaks after a suspected fall (robot can't reach the person, WebRTC drops, pose is lost), the system escalates. It never goes quiet.

## 1. Calendar at a glance

| Milestone | Due | Done when |
|---|---|---|
| **M1: Go2 camera test** | Sun Oct 4 | Go2 frames and webcam frames both come through one `FrameSource` interface, with MediaPipe pose drawn on them. Latency and FPS are measured. One motion command reaches the robot. |
| **M2: Fall detector on webcam** | Sun Oct 11 | My detector finds falls live on the webcam and on recorded clips. There's a first precision/recall number. |
| **M3: Robot behavior + alerts + eval** | Sun Oct 18 | The full loop works: fall → search → approach → check-in → SMS or resolve. The dashboard is live. Eval report covers webcam and Go2 clips. |
| **M4: Video + submission** | Fri Oct 23 | Demo video, README, and final `AI_USAGE.md` are done. **Submit by Thu Oct 22** so there's a day of buffer. |

**Start today, not later, because these have lead times:**
1. **Twilio:** create the account, verify the family phone numbers, and check what US SMS registration currently requires (A2P 10DLC or toll-free verification can take days to weeks).
2. **Go2:** turn off automatic firmware updates and write down the current firmware version (see risk G5).
3. **Consent:** get written OK from anyone who appears in recorded clips. Keep the forms outside the repo.

## 2. Proposed layout

```
watchdog/                     # repo root
  watchdog/
    __init__.py               [AI-SCAFFOLD]
    config.py                 [AI-SCAFFOLD]  loads config/default.yaml + .env
    logging_setup.py          [AI-SCAFFOLD]
    datatypes.py              [BOUNDARY → AI-SCAFFOLD fields only] Frame, PoseResult, FallEvent, Alert
    camera_source/
      base.py                 [AI-SCAFFOLD]  FrameSource protocol + make_source()
      webcam.py               [AI-SCAFFOLD]
      file_source.py          [AI-SCAFFOLD]  video-file replay (for tests and eval)
      go2.py                  [BOUNDARY → recommend MINE with guidance]
    pose/
      mediapipe_pose.py       [AI-SCAFFOLD]  thin wrapper around the MediaPipe API
    fall_detector/
      features.py             [MINE]
      classifier.py           [MINE]
    robot_behavior/
      controller.py           [AI-SCAFFOLD]  RobotController protocol + FakeRobot test double
      go2_controller.py       [BOUNDARY → recommend MINE with guidance]
      state_machine.py        [MINE]
    alerts/
      policy.py               [MINE]  when, whether, and how to escalate
      sms.py                  [AI-SCAFFOLD]  Twilio send wrapper
      event_log.py            [AI-SCAFFOLD]  append-only JSONL log
      dashboard.py + templates/ [AI-SCAFFOLD]  Flask
    eval/
      run_eval.py             [AI-SCAFFOLD]  loops over clips, collects predictions
      metrics.py              [BOUNDARY → recommend MINE]  matching + precision/recall
    main.py                   [AI-SCAFFOLD wiring; the logic it calls is MINE]
  scripts/                    [AI-SCAFFOLD]  hardware smoke tests (go2_camera_check.py, latency_probe.py, record_clip.py)
  tests/                      [AI-SCAFFOLD fixtures/stubs; I write core assertions]
  config/default.yaml         [AI-SCAFFOLD]
  data/raw/                   (gitignored) clips
  data/labels/labels.csv      (committed, holds no footage)
  requirements.txt            [AI-SCAFFOLD]
```

Why the BOUNDARY recommendations go the way they do: the Go2 adapter and controller are the hardware-integration code that makes this project different from a webcam demo, so judges will likely ask about them. Writing them myself, with guidance, is the safer disclosure story. Pinning the dataclass fields is plain typing with no logic, so it's scaffold, but *which* fields exist is my call.

---

## 3. Modules

The signatures below are **interfaces only**. They give the shape, not an implementation.

### 3.1 `camera_source`: one interface for Go2, webcam, and file

**Interface**
```python
@dataclass
class Frame:
    image: np.ndarray        # BGR, HxWx3, uint8
    t_capture: float         # time.monotonic() when the frame arrived
    source: str              # "go2" | "webcam" | "file:<name>"

class FrameSource(Protocol):
    def start(self) -> None
    def read(self, timeout: float = 1.0) -> Frame | None   # newest frame; drops stale ones; None on timeout
    def is_healthy(self) -> bool                            # False if no frame for > cfg.stale_after_s
    def stop(self) -> None

def make_source(cfg: CameraConfig) -> FrameSource          # picks one by cfg.kind
```
- The Go2 library runs on `asyncio` (aiortc) and delivers frames in a callback. `Go2Source` runs that loop in a background thread and puts frames in a **size-1 "latest frame" slot**. Everything downstream stays synchronous and always sees the freshest frame. No queue builds up, so latency doesn't grow over time.
- `FileSource` makes tests and eval repeatable and works without the robot.

**Key technical risk:** the Go2 WebRTC link (`go2-webrtc-connect`) on Windows. That includes installing aiortc/PyAV, connecting only while the Unitree phone app is closed, and the robot's firmware version.

**Test that proves it works**
- `tests/test_camera_source.py`: `FileSource` on a short clip in the repo returns frames in order with rising timestamps, and returns `None` after the end.
- `scripts/go2_camera_check.py` (manual, hardware): runs 5 minutes, logs FPS, frame-age percentiles, and dropouts. **Passes if** FPS ≥ 15, p95 frame age ≤ 500 ms, and the stream recovers on its own after I switch the robot's Wi-Fi off and on.
- `scripts/latency_probe.py`: point the Go2 camera at a laptop screen showing a millisecond clock, compare the clock in the frame to wall time, and record glass-to-glass latency.

**Fallback:** if Go2 video isn't stable by Oct 4, **the laptop webcam becomes the room camera** (fixed and elevated, which is also a better viewing angle). The robot is then only the responder, driven by fall events from the webcam. The demo still tells the full story.

### 3.2 `pose`: MediaPipe Pose wrapper **[AI-SCAFFOLD]**

**Interface**
```python
@dataclass
class PoseResult:
    landmarks: np.ndarray | None   # (33, 4): x, y normalized [0,1], z, visibility; None if no person
    t_capture: float               # copied from Frame
    image_size: tuple[int, int]    # (w, h)

class PoseEstimator:
    def __init__(self, model_complexity: int = 1, min_detection_conf: float = 0.5,
                 min_tracking_conf: float = 0.5): ...
    def process(self, frame: Frame) -> PoseResult
    def close(self) -> None
```

**Key technical risk:** the MediaPipe API is changing (the old `mp.solutions.pose` vs the new Tasks `PoseLandmarker`), so pin one version in `requirements.txt`. The bigger risk is **poor landmark quality on people lying down** and in dim light. The model was trained mostly on upright people.

**Test that proves it works**
- A unit test on 3 stored still images (standing, sitting, lying): the landmarks are not None and visibility is above 0.5 on the torso points.
- Manual check: an overlay script draws the skeleton on live Go2 and webcam video and records processing ms per frame. **Passes if** it stays under 50 ms per frame on the laptop CPU at 640 px width.

**Fallback:** if lying-down landmarks are too noisy, have the detector lean on **bounding-box geometry** (built from whatever landmarks are visible) and on **"person missing or low" over time** instead of joint angles. If CPU is too slow, drop to `model_complexity=0` and/or run pose on every 2nd frame.

### 3.3 `fall_detector`: features + classifier **[MINE]**

**Interface**
```python
FeatureVector = dict[str, float]

def compute_features(window: Sequence[PoseResult]) -> FeatureVector   # sliding window, about 1–2 s

@dataclass
class FallEvent:
    t_detected: float
    confidence: float          # 0..1
    reason: str                # human-readable, e.g. "fast hip drop + horizontal torso + still 4s"
    frame: Frame | None        # snapshot for dashboard (never committed)

class FallDetector:
    def __init__(self, cfg: FallConfig): ...
    def update(self, pose: PoseResult) -> FallEvent | None   # called once per frame
    def reset(self) -> None
```

**Approach (guidance only, I write the code):** use two stages.
1. **Impact candidate:** a fast downward change in body height, such as the hip-center y velocity normalized by body size. It's paired with the torso going from upright to horizontal and the bounding-box aspect ratio flipping from tall to wide.
2. **Confirmation:** the person *stays* low or horizontal with little motion for N seconds. This separates a fall from sitting down fast or bending over.

Start with hand-tuned thresholds I can explain line by line. Optional after M2: fit a small scikit-learn model (logistic regression or a shallow tree) on the same features and compare the two in eval. Normalize features by body scale so distance from the camera doesn't matter.

**Key technical risk:** **false alarms** from lying on a couch, picking something up, or exercising, versus **missed falls** that are slow or happen partly off-screen. The Go2's **low camera (~30–40 cm), looking up** distorts torso angle, so thresholds tuned on the webcam may not transfer.

**Test that proves it works**
- `tests/test_fall_detector.py`: fixtures are **synthetic landmark sequences** (standing → rapid drop → lying still, standing → slow sit, lying from the start, person missing). Claude may scaffold the sequence generators. I write the assertions: fires once on the fall, does not fire on the sit, does not fire before the confirmation time.
- Clip eval (section 3.6): **M2 target is recall ≥ 0.85 and precision ≥ 0.80** on my webcam clip set, and **≤ 1 false alarm per 30 min** of everyday-activity footage.

**Fallback:** if confirmation is unreliable, lengthen the confirmation window and **let the check-in be the confirmation step**. A false alarm then costs one "Are you OK?" instead of one SMS. If the Go2 angle breaks my thresholds, keep separate thresholds for each camera in config.

### 3.4 `robot_behavior`: search / approach / check-in state machine **[MINE]** (controller adapter is BOUNDARY)

**Interface**
```python
class RobotController(Protocol):            # [AI-SCAFFOLD] protocol + FakeRobot
    def stand(self) -> None
    def move(self, vx: float, vy: float, vyaw: float) -> None   # m/s, m/s, rad/s; clamped to safety limits
    def stop(self) -> None
    def say(self, text: str) -> bool        # Go2 speaker if it works, laptop TTS otherwise
    def is_connected(self) -> bool

class BState(Enum):
    WATCHING, SEARCHING, APPROACHING, CHECKING_IN, RESOLVED, ESCALATED

@dataclass
class BehaviorOutput:
    state: BState
    alert_request: AlertRequest | None      # handed to alerts.policy

class Behavior:
    def __init__(self, robot: RobotController, cfg: BehaviorConfig,
                 clock: Callable[[], float] = time.monotonic): ...
    def on_fall(self, event: FallEvent) -> None
    def step(self, pose: PoseResult) -> BehaviorOutput     # called once per frame
```

**Approach (guidance only):**
- **SEARCHING:** rotate in place in steps (turn → stop → look), because a camera that is moving blurs and lags.
- **APPROACHING:** visual servoing. Turn to center the person's box, then creep forward until the box height reaches a target or a minimum distance is reached.
- **CHECKING_IN:** say "Are you OK? Raise a hand if you're OK," then watch pose for a raised hand (reusing the pose module). A voice reply is a stretch goal.
- **Every state has a timeout.** Timeout, disconnect, or lost person all lead to ESCALATED (fail toward alerting).
- The injected `clock` makes timeouts testable without waiting in real time.

**Key technical risk:** **control with 300–500 ms of lag.** Continuous steering will overshoot. Use stop-and-look steps and slow speeds. **Physical safety near a person on the floor:** cap speed at about 0.3 m/s, set a minimum stand-off of about 1 m, keep the Go2's built-in obstacle avoidance on, and keep a dashboard "STOP" button plus the physical remote in hand during every test.

**Test that proves it works**
- `tests/test_state_machine.py` with `FakeRobot` + fake clock + scripted `PoseResult`s. I write the assertions: fall → SEARCHING; person seen → APPROACHING; box large enough → CHECKING_IN; hand raised → RESOLVED; no response for T → ESCALATED; disconnect mid-approach → ESCALATED; `move()` never exceeds the speed limits.
- Hardware run (M3): 10 staged trials in the living room from 3 start positions. Record the success rate and the time from fall to check-in.

**Fallback:** if approach is unreliable, cut it down to **turn toward the person + speak** (no walking). If Go2 motion over WebRTC doesn't work from Windows, **the robot becomes a scripted responder** triggered from the app (clearly disclosed), and all the logic runs against `FakeRobot` for the demo of the state machine.

### 3.5 `alerts`: Twilio SMS + Flask dashboard (policy is **[MINE]**)

**Interface**
```python
@dataclass
class Alert:
    level: Literal["info", "urgent"]
    message: str
    t_event: float
    snapshot_path: str | None     # local only; not sent by SMS by default (privacy)

class AlertPolicy:                                     # [MINE]
    def decide(self, out: BehaviorOutput, now: float) -> Alert | None
    # I decide: which states escalate, cooldown/dedup, a follow-up if no one acknowledges, "resolved" messages

class SmsNotifier:                                     # [AI-SCAFFOLD]
    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_numbers: list[str]): ...
    def send(self, alert: Alert) -> bool               # retries; logs failures; never raises into main loop

class EventLog:                                        # [AI-SCAFFOLD]
    def append(self, kind: str, payload: dict) -> None
    def recent(self, n: int = 50) -> list[dict]

def create_app(log: EventLog, stop_callback: Callable[[], None]) -> Flask   # [AI-SCAFFOLD]
# routes: / (status + recent events + snapshot), /ack (caregiver acknowledges), /stop (robot e-stop)
```

**Key technical risk:** **SMS delivery gating.** Twilio trial limits and US carrier registration may block real texts by Oct 18. A second risk is an alert storm, where one fall sends 20 texts.

**Test that proves it works**
- `tests/test_alert_policy.py`: I write the assertions. No alert on RESOLVED. Exactly one urgent alert on ESCALATED. Cooldown suppresses duplicates. An unacknowledged alert re-sends after T.
- `tests/test_sms.py` (scaffold): uses a mocked Twilio client to check the message format and that failures return False without raising.
- Manual check: a real SMS reaches my phone within 10 s of ESCALATED. The dashboard updates, and `/stop` halts the robot.

**Fallback:** if SMS is blocked, send **email over SMTP** or a push service (e.g. ntfy) behind the same `send(alert)` interface, and show the dashboard alert in the demo. Say so on camera.

### 3.6 `eval`: precision/recall on labeled clips

**Interface**
```python
# data/labels/labels.csv
# clip, camera(go2|webcam|public), lighting(bright|dim|backlit), has_fall(0/1), fall_start_s, fall_end_s, activity, notes

def run_clip(path: str, detector_factory: Callable[[], FallDetector]) -> list[FallEvent]   # [AI-SCAFFOLD]
def match(preds: list[FallEvent], label: Label, tolerance_s: float = 3.0) -> tuple[int, int, int]  # TP, FP, FN  [recommend MINE]
def summarize(results) -> Report   # precision, recall, F1, mean detection delay, false alarms/hour,
                                   # broken down by camera and lighting                               [recommend MINE]
```
Output: `eval/report.md` + a confusion table. This goes straight into the video and README.

**Clip plan** (all stored in `data/raw/`, gitignored):
- My own clips: around 20 falls onto a mattress or crash pad and around 20 everyday activities (sit fast, lie on couch, bend, tie shoes, exercise). Each set is split between webcam and Go2 camera and across 3 lighting conditions. **Only people who consented; nobody elderly performs falls.**
- A public set for a sanity check, e.g. the UR Fall Detection dataset (check license terms and cite it). It's labeled "public" so it doesn't inflate my own-clip numbers.
- **Hold out** about 30% of my clips that I never tune on. The headline numbers come from those.

**Key technical risk:** a small, staged dataset gives optimistic numbers. Reduce this with the held-out split, the per-lighting and per-camera breakdown, and saying the limitation honestly.

**Test that proves it works:** `tests/test_metrics.py` has hand-computed toy cases (one TP, one FP outside the tolerance, one missed fall). I write them.

**Fallback:** if Go2 clips are scarce, report webcam results fully and Go2 results as a small pilot, labeled that way.

---

## 4. Go2-specific risk register

| # | Risk | What it looks like | Mitigation | Early check |
|---|---|---|---|---|
| G1 | **Latency** | 300–500 ms+ glass-to-glass, so fall detection feels late and steering overshoots | Latest-frame slot (no queue); stop-and-look motion; detector uses timestamps, not frame counts; log frame age every second | `latency_probe.py` by Oct 3 |
| G2 | **WebRTC drops** | Stream freezes, callback stops, Wi-Fi roams | `is_healthy()` checks frame age; reconnect with backoff; the drop is logged; **a drop during an active fall response → ESCALATE** | Wi-Fi off/on test in M1 |
| G3 | **Single client** | Connection fails while the Unitree app is open | Close the app during runs; document it in README | M1 |
| G4 | **Lighting** | Dim evening rooms, backlit windows, no IR on the camera, so pose disappears | Record test clips in 3 lighting conditions; optional contrast boost (CLAHE) before pose; a low-visibility pose counts as "uncertain", not "fine"; report per lighting condition in eval | Clip session in M2 |
| G5 | **Firmware drift** | An auto-update changes the WebRTC protocol and the library breaks | Turn off updates now; record the firmware version and library commit in README | Today |
| G6 | **Low camera angle** | The camera looks up; a person lying close fills the frame or is cut off | Thresholds per camera; a stand-off distance so the whole body fits in frame | M2/M3 |
| G7 | **Motion blur** | Detection while walking is garbage | Detector runs only while the robot is stationary (WATCHING) | M3 |
| G8 | **Battery** | Around 1–2 h per charge | Schedule hardware sessions; charge before filming day | Ongoing |
| G9 | **Windows toolchain** | aiortc/PyAV/PyAudio wheels fail on Windows | Try install on day 1; fall back to WSL2 or to a different Python patch version if needed | Today |
| G10 | **Safety** | Robot bumps a person on the floor | Speed cap, stand-off, obstacle avoidance on, e-stop in dashboard + remote, soft mat for staged falls | Before first motion test |

## 5. Day-by-day

**M1: Tue Sep 29 → Sun Oct 4 (Go2 camera test)**
- Sep 29–30: [AI-SCAFFOLD] folders, `requirements.txt`, config, logging, `FrameSource` protocol, webcam + file sources, pose wrapper, smoke scripts. Me: start Twilio and consent paperwork, turn off Go2 updates, install the Go2 library.
- Oct 1–2: Me: write `Go2Source` (with guidance). Get frames, then the latest-frame slot, then the health check.
- Oct 3: Me: latency probe, 5-min stability run, Wi-Fi drop test. **Also send one `move()` and `stop()`** to reduce M3 risk early.
- Oct 4: pose overlay on both sources; record the first practice clips. **Explain-back: camera_source + pose.** M1 review.

**M2: Mon Oct 5 → Sun Oct 11 (fall detector on webcam)**
- Oct 5–6: Me: features. Claude reviews. Synthetic-sequence test fixtures (scaffold) + my assertions.
- Oct 7–8: Me: two-stage classifier + thresholds. Record the labeled clip set (mattress, consent, 3 lighting conditions).
- Oct 9: [AI-SCAFFOLD] eval runner. Me: metrics + first report.
- Oct 10: tune on the tuning split only; live webcam demo.
- Oct 11: **Explain-back: fall_detector.** M2 review. Decide: rules only, or rules + small model.

**M3: Mon Oct 12 → Sun Oct 18 (robot behavior + alerts + eval)** _(heaviest week; alerts plumbing gets scaffolded early)_
- Oct 12: [AI-SCAFFOLD] `RobotController` protocol, `FakeRobot`, SMS wrapper, event log, Flask dashboard. Me: `Go2Controller` (with guidance).
- Oct 13–14: Me: state machine + tests against `FakeRobot`.
- Oct 15: Me: alert policy + tests; real SMS end to end.
- Oct 16: hardware trials (10 runs), safety checklist first.
- Oct 17: Go2-camera clips into eval; final report on the held-out split.
- Oct 18: **Explain-back: robot_behavior, alerts, eval.** M3 review. **Feature freeze.**

**M4: Mon Oct 19 → Fri Oct 23 (video + submission)**
- Oct 19: script the video (problem → how it works → live demo → eval numbers → limitations → AI disclosure). Check the current CAC video length and format rules.
- Oct 20: film (charged battery, good light, one dim-light shot to show honesty about limits).
- Oct 21: edit; README ([AI-SCAFFOLD] formatting, my content); finalize `AI_USAGE.md`.
- Oct 22: **submit.** Oct 23 is buffer only.

## 6. Cut list (drop from the top if behind)

1. Voice reply at check-in (hand-raise only)
2. Learned classifier (keep rules)
3. Walking approach (turn + speak only)
4. Go2 as the camera (webcam as room camera, Go2 as responder)
5. Real SMS (email or push + dashboard)

The **webcam fall detector + state machine on FakeRobot + dashboard alert + eval report** is never cut. That is the minimum project that can be submitted.

## 7. Privacy and disclosure checklist

- [ ] No footage, snapshots, or `.env` in git. Check `git status` before every commit.
- [ ] SMS text carries no image by default; the dashboard is reachable only on the local network.
- [ ] Consent on file for everyone in the clips.
- [ ] Wording is "safety companion prototype", not a medical device.
- [ ] `AI_USAGE.md` row for every session; video includes a short AI-use disclosure.
- [ ] Explain-back done for each module before sign-off.

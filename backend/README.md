# ACEYE editable backend

`backend/run.py` starts the local command server and serves `frontend/`. The Python code is the editable orchestration layer: replace an adapter in `aceye/adapters/`, add a module, or change the policy in `pipeline.py` without rebuilding the native launcher.

Run `scripts/run.ps1` after creating the environment with `scripts/install_backend.ps1`. Add `-Models` only when installing optional model runtimes. Model adapters accept local weight paths and report missing dependencies or weights through the UI; they do not silently download or fabricate results.

Implemented adapters are YOLO26 segmentation plus ByteTrack, PaddleOCR 3.x, SAM 2.1 Tiny and Qwen3-VL-2B through local Transformers or an OpenAI-compatible endpoint. The Rhythm path submits its timestamped key actions through the native SendInput bridge.

The command console accepts `/start`, `/stop`, `/fps 30`, `/conf 0.4`, `/ocr on`, `/overlay on`, `/goal ...`, `/reason`, `/sam`, `/dryrun on`, `/emergency`, and `/help`.

## Rhythm Demo 1 (Muse Dash)

Rhythm consumes the `SharedFrameHub` published by the general pipeline. It
does not create a second DXGI/Desktop Duplication object. When a pipeline is
already running, Rhythm reuses its capture. Otherwise, starting Rhythm starts
the existing pipeline in capture-only mode for this run, with no model loading.

From the extracted `dist/ACEYE` directory, run `ACEYE.exe`. The launcher opens
the local UI with its authorization token. Click **启动 Rhythm**; a separate
click on **一键启动** is not required. Open Muse Dash, start a simple song, and
keep the game focused while notes are appearing. The pause menu is not a test
of note detection.

The same start/status operations are available through the API:

```powershell
$h = @{ "X-ACEYE-Token" = "<token from /api/health>" }
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/rhythm/start -Headers $h -Body '{"game":"muse_dash"}' -ContentType 'application/json'
Invoke-RestMethod http://127.0.0.1:8765/api/rhythm/status -Headers $h
```

Rhythm always uses the native Windows SendInput bridge; there is no simulated
input switch in the service. `scheduled_actions` counts scheduled actions;
`real_input_count` counts successfully submitted real key presses, not hits
confirmed by the game. The Muse Dash profile is
`config/profiles/muse_dash.json`. Its ROI is `[x, y, width, height]` relative
to the captured desktop; `[0, 0, 0, 0]` means the full screen until the game
window position is calibrated.

The native bridge sends bounded key-down/key-up pairs.
Press **F8** at any time to cancel queued actions and release active keys; the
status changes to `safety-stopped`. `/api/rhythm/stop` is also a hard stop.

The UI displays capture FPS, detected notes, scheduled actions, real input
count, the input bridge return status, and errors separately. Status `0` means
the native bridge accepted the input; it does not confirm a successful hit.
The runtime logs note position/lane/estimated hit time, the scheduled key and
delay, and real key submission timestamps and return status.

During a playing song, record `capture_fps`, `frames`, `detected_notes`,
`scheduled_actions`, `real_input_count`, `sendinput_last_status`,
`input_scheduling_jitter_p95_ms`, and `error` from `/api/rhythm/status`.
Both action counters should grow and the character should visibly respond.
Check that the profile's keys match Muse Dash's keyboard settings. If Windows
rejects input, compare the privilege levels of ACEYE and Muse Dash: when the
game runs as administrator, ACEYE must run at the same level. Press F8 to end
the test and confirm the real input count stops increasing.

Automated tests and successful SendInput submission do not constitute an
in-game acceptance result. A complete song must still be observed in the
running game before declaring the Muse Dash demo verified.

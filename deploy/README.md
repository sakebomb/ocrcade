# Deploying OCRcade to kepler

Everything runs on the Pi (`kepler`); the GPU VLM is reached over HTTP.

## First-time setup
```bash
# 1. code
rsync -a --exclude .git --exclude __pycache__ ./ sakebomb@kepler:~/ocrcade/
cd ~/ocrcade && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
# (hailo_platform + picamera2 come from the system image, not pip)

# 2. secrets + runtime data (NOT in git)
cp .env.example .env                      # fill OCRCADE_RTB_* 
cp teams.example.json teams.json          # real bot creds
cp flag_config.example.json flag_config.json
# flags/flagN.txt (RTB tokens) and puzzles/level1/flag1.txt (puzzle answer)
chmod 600 .env teams.json flag_config.json flags/*.txt

# 3. konami button calibration (camera-specific)
python3 challenges/calibrate_konami.py    # writes challenges/konami_config.json

# 4. services
sudo cp deploy/ocrcade-exec.service deploy/ocrcade-vision.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ocrcade-exec ocrcade-vision
```

## Cutover from the old service
Run the new units under different names first, validate all 6 puzzles, then disable
the old `ocrcade-vision`/`ocrcade-exec` and enable these. Keep the old code archived.

## Config
`config.yaml` — VLM endpoint/model, gating thresholds (`blur_min`, `stable_frames`),
camera `rotate`. Tune the gate on-device with real lighting.

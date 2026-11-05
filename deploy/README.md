# Deploying OCRcade to kepler

Everything runs on the Pi. A remote code reader, when you use one, is reached over HTTP.
`~/ocrcade` on kepler is a git checkout on branch `deploy`. You update it by pushing to
it from a clone of this repo; it never needs GitHub credentials of its own.

## Updating (normal use)
```bash
deploy/deploy.sh            # deploy main
deploy/deploy.sh <commit>   # deploy any ref; the previous commit is a rollback
```
The script pushes the commit, installs any changed systemd unit, restarts both
services, and fails unless `/health` answers and both units are active. Git refuses the
push if a tracked file was edited on kepler; fix the change in the repo instead.

## One-time setup on your machine
```bash
git remote add kepler ssh://<user>@<pi-host>/home/<user>/ocrcade
```

## First-time setup on a fresh Pi
The services use the system `python3`. OpenCV, `picamera2`, `hailo_platform` and `lgpio`
come from Raspberry Pi OS packages; check `requirements.txt` for the rest.
```bash
# 1. code: an empty checkout that accepts pushes
git init -b deploy ~/ocrcade
git -C ~/ocrcade config receive.denyCurrentBranch updateInstead
# then, from your machine: git push kepler main:refs/heads/deploy

# 2. secrets + runtime data (NOT in git)
cp .env.example .env                      # fill OCRCADE_RTB_* 
cp teams.example.json teams.json          # real bot creds
cp flag_config.example.json flag_config.json
# flags/flagN.txt (RTB tokens) and puzzles/level1/flag1.txt (puzzle answer);
# puzzles/level1/flag1.txt must be identical to flags/flag1.txt
chmod 600 .env teams.json flag_config.json flags/*.txt

# 3. konami button calibration (camera-specific)
python3 challenges/calibrate_konami.py    # writes challenges/konami_config.json

# 4. services
sudo cp deploy/ocrcade-exec.service deploy/ocrcade-vision.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ocrcade-exec ocrcade-vision
```

## Config
`config.yaml` — VLM endpoint/model, gating thresholds (`blur_min`, `stable_frames`),
camera `rotate`. Tune the gate on-device with real lighting.

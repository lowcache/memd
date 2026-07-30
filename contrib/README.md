# Standalone systemd User Service & Timer

This directory contains standalone systemd user service and timer units for running `memd sweep` periodically on non-Nix systems.

## Files

- `memd-sweep.service`: Runs the sweep command.
- `memd-sweep.timer`: Triggers the service 5 minutes after boot, and every 30 minutes thereafter.

## Installation

1. Copy the unit files to the systemd user configuration directory:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp contrib/memd-sweep.* ~/.config/systemd/user/
   ```

2. Reload the systemd user daemon:
   ```bash
   systemctl --user daemon-reload
   ```

3. Enable and start the timer:
   ```bash
   systemctl --user enable --now memd-sweep.timer
   ```

## Verification

To verify that the timer is active and scheduled:
```bash
systemctl --user list-timers --all | grep memd-sweep
```

## Customization

### Executable Path
The systemd user manager does not inherit your interactive shell's `PATH`, and its fixed search path excludes `~/.local/bin` — where `pip install --user` and `pipx` put console scripts. The shipped unit therefore uses an explicit path:

```systemd
ExecStart=%h/.local/bin/memd sweep
```

(`%h` is a systemd specifier resolved to your home directory at runtime, so no username is hardcoded.)

If memd is instead on the system path — a Nix profile install, a distro package, or `/usr/local/bin` — change it to plain `ExecStart=memd sweep`. Confirm which applies with `command -v memd`. If memd lives in a virtualenv, use that venv's absolute `bin/memd`.

Edit the unit directly or use a drop-in override (`systemctl --user edit memd-sweep.service`), then `systemctl --user daemon-reload`.

### Changing Sweep Frequency
To change how often the sweep runs, edit the `[Timer]` section of `~/.config/systemd/user/memd-sweep.timer` (e.g. changing `OnUnitActiveSec` or `RandomizedDelaySec`), then run:
```bash
systemctl --user daemon-reload
```

## Uninstallation

To remove the service and timer:
```bash
systemctl --user disable --now memd-sweep.timer
rm -f ~/.config/systemd/user/memd-sweep.service ~/.config/systemd/user/memd-sweep.timer
systemctl --user daemon-reload
```

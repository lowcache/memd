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

### Local/User Bin PATH Override
Since systemd user manager services run in an environment that may not inherit your interactive shell's `PATH` (such as `~/.local/bin`), `ExecStart=memd sweep` might fail if `memd` is installed via `pip install --user`.

To specify the absolute path without hardcoding a username, edit `~/.config/systemd/user/memd-sweep.service` or use a drop-in override (`systemctl --user edit memd-sweep.service`) to set:
```systemd
[Service]
ExecStart=%h/.local/bin/memd sweep
```
(`%h` is a systemd specifier resolved to your home directory at runtime).

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

# Home-manager module for memd.
#
# Exported from the flake as `homeManagerModules.default` (and `.memd`) with
# the flake's own package baked in as the default for `services.memd.package`,
# so consumers only need:
#
#   imports = [ memd.homeManagerModules.default ];
#   services.memd.enable = true;
#
# The outer function is applied by the flake (passing `self`); the inner
# function is the actual home-manager module.
{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.memd;

  # PATH for the sweep unit. Ports the semantics of the previous hand-rolled
  # user unit (which used ~/.local/bin/memd with user/system profiles on
  # PATH): package bin first, then the HM/user profile, the system profile,
  # and ~/.local/bin (the `claude` CLI typically lives there).
  sweepPath = lib.concatStringsSep ":" [
    "${cfg.package}/bin"
    "/etc/profiles/per-user/${config.home.username}/bin"
    "/run/current-system/sw/bin"
    "${config.home.homeDirectory}/.local/bin"
  ];
in
{
  options.services.memd = {
    enable = lib.mkEnableOption "memd, the agent-driven project-memory curator";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.memd;
      defaultText = lib.literalExpression "memd.packages.\${system}.memd";
      description = "The memd package to use.";
    };

    sweep = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Run `memd sweep` periodically via a systemd user timer.";
      };

      interval = lib.mkOption {
        type = lib.types.str;
        default = "30min";
        description = "OnUnitActiveSec interval between sweep runs.";
      };

      onBoot = lib.mkOption {
        type = lib.types.str;
        default = "5min";
        description = "OnBootSec delay before the first sweep after boot.";
      };

      randomizedDelay = lib.mkOption {
        type = lib.types.str;
        default = "2min";
        description = "RandomizedDelaySec jitter applied to each activation.";
      };
    };

    installClaudeHooks = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Run `memd install-hooks` during home-manager activation. This
        idempotently edits ~/.claude/settings.json in place.
      '';
    };
  };

  config = lib.mkIf cfg.enable (lib.mkMerge [
    {
      home.packages = [ cfg.package ];
    }

    (lib.mkIf cfg.sweep.enable {
      systemd.user.services.memd-sweep = {
        Unit = {
          Description = "memd inbox sweep";
        };
        Service = {
          Type = "oneshot";
          ExecStart = "${cfg.package}/bin/memd sweep";
          Nice = 10;
          Environment = [ "PATH=${sweepPath}" ];
        };
      };

      systemd.user.timers.memd-sweep = {
        Unit = {
          Description = "Periodic memd inbox sweep";
        };
        Timer = {
          OnBootSec = cfg.sweep.onBoot;
          OnUnitActiveSec = cfg.sweep.interval;
          RandomizedDelaySec = cfg.sweep.randomizedDelay;
        };
        Install = {
          WantedBy = [ "timers.target" ];
        };
      };
    })

    (lib.mkIf cfg.installClaudeHooks {
      # `memd install-hooks` is idempotent; guard on the binary existing so a
      # broken/garbage-collected package cannot fail the whole activation.
      home.activation.memdInstallClaudeHooks = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        if [ -x "${cfg.package}/bin/memd" ]; then
          run "${cfg.package}/bin/memd" install-hooks
        else
          verboseEcho "memd binary not found at ${cfg.package}/bin/memd; skipping install-hooks"
        fi
      '';
    })
  ]);
}

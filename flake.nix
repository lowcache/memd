{
  description = "memd — agent-driven project-memory curator for AI CLI sessions";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
      # NOTE: must match `__version__` in memd/__init__.py
      version = "0.2.0";
    in
    {
      packages = forAll (pkgs: rec {
        memd = pkgs.stdenv.mkDerivation {
          pname = "memd";
          inherit version;
          src = ./.;
          nativeBuildInputs = [ pkgs.python3 ];
          dontBuild = true;
          installPhase = ''
            site=$out/${pkgs.python3.sitePackages}
            mkdir -p $site $out/bin
            cp -r memd $site/memd
            cat > $out/bin/memd <<EOF
            #!${pkgs.python3.interpreter}
            import sys
            sys.path.insert(0, "$site")
            from memd.cli import main
            main()
            EOF
            chmod 755 $out/bin/memd
          '';
          meta.description = "Agent-driven project memory curator for AI CLI sessions";
        };
        default = memd;
      });

      # Run straight from the flake: `nix run .# -- status`
      apps = forAll (pkgs: rec {
        memd = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.memd}/bin/memd";
        };
        default = memd;
      });

      checks = forAll (
        pkgs:
        let
          memd = self.packages.${pkgs.system}.memd;
          pythonWithPytest = pkgs.python3.withPackages (ps: [ ps.pytest ]);
        in
        {
          # Basic sanity: the installed binary runs at all.
          smoke =
            pkgs.runCommand "memd-smoke"
              {
                nativeBuildInputs = [ memd ];
              }
              ''
                memd --version
                memd --help > /dev/null
                touch $out
              '';
        }
        # Full test suite — only when tests/ is present in the flake source
        # (flakes in a git repo only see tracked / intent-to-add files).
        // (
          if builtins.pathExists ./tests then
            {
              pytest =
                pkgs.runCommand "memd-pytest"
                  {
                    nativeBuildInputs = [
                      pythonWithPytest
                      pkgs.git
                    ];
                  }
                  ''
                    cp -r ${self} src
                    chmod -R u+w src
                    cd src

                    # Isolate from any real user config/state.
                    export HOME="$TMPDIR/home"
                    export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
                    export XDG_STATE_HOME="$TMPDIR/xdg-state"
                    mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME"

                    python -m pytest tests/ -q
                    touch $out
                  '';
            }
          else
            builtins.trace
              "memd: tests/ not found in flake source; skipping pytest check (git add tests/ to enable)"
              { }
        )
      );

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.pytest ]))
            pkgs.git
          ];
        };
      });

      homeManagerModules = rec {
        memd = import ./nix/home-manager.nix { inherit self; };
        default = memd;
      };
    };
}

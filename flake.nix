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
      # Read the version out of memd/__init__.py rather than restating it. The
      # previous "NOTE: must match" comment did not stop the two from drifting:
      # this said 0.2.0 (a version never tagged) while the package said 0.4.2,
      # and the v0.4.2 tag shipped that way, so anyone building the flake got a
      # derivation labelled memd-0.2.0. A comment is not a constraint; this is.
      version =
        let
          initPy = builtins.readFile ./memd/__init__.py;
          matches = builtins.filter (m: m != null) (
            map (line: builtins.match ''__version__ = "([^"]+)"'' line) (
              nixpkgs.lib.splitString "\n" initPy
            )
          );
        in
        if matches == [ ] then
          throw "memd flake: no __version__ line found in memd/__init__.py"
        else
          builtins.head (builtins.head matches);
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
                # Both sides now read memd/__init__.py, so they cannot disagree
                # about the NUMBER. What this still catches is the `--version`
                # output FORMAT changing out from under packagers: nixpkgs-style
                # versionCheckHook greps this string, and llm-agents.nix does.
                got="$(memd --version)"
                if [ "$got" != "memd ${version}" ]; then
                  echo "memd: version mismatch: --version says '$got', flake built ${version}" >&2
                  exit 1
                fi
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

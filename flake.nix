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
    in
    {
      packages = forAll (pkgs: rec {
        memd = pkgs.stdenv.mkDerivation {
          pname = "memd";
          version = "0.1.0";
          src = ./.;
          nativeBuildInputs = [ pkgs.python3 ];
          dontBuild = true;
          installPhase = ''
            mkdir -p $out/bin
            install -m755 memd.py $out/bin/memd
            patchShebangs $out/bin/memd
          '';
          meta.description = "Agent-driven project memory curator for AI CLI sessions";
        };
        default = memd;
      });

      # Run straight from the flake: `nix run .# -- status`
      apps = forAll (pkgs: rec {
        memd = {
          type = "app";
          program = "${self.packages.${pkgs.system}.memd}/bin/memd";
        };
        default = memd;
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell { packages = [ pkgs.python3 ]; };
      });
    };
}

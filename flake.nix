{
  description = "TRCC Linux — Thermalright LCD/LED Control Center";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;
      in {
        packages.default = python.pkgs.buildPythonApplication {
          pname = "trcc-linux";
          version = "9.7.2";
          pyproject = true;

          src = ./.;

          build-system = [ python.pkgs.hatchling ];

          dependencies = with python.pkgs; [
            pyside6
            numpy
            psutil
            pyusb
            pyudev          # Linux hotplug (live attach/detach + coldplug) — #139
            click
            typer
            fastapi
            uvicorn
            python-multipart
            prompt-toolkit
            sounddevice
            certifi
          ];

          optional-dependencies = {
            nvidia = [ python.pkgs.nvidia-ml-py ];
          };

          nativeBuildInputs = [ pkgs.makeWrapper ];

          propagatedBuildInputs = [
            pkgs.portaudio
            pkgs.libusb1
            pkgs.p7zip
            pkgs.sg3_utils
            pkgs.ffmpeg
          ];

          # Skip tests during build (they need USB devices)
          doCheck = false;

          postInstall = ''
            # udev rules
            install -Dm644 packaging/udev/99-trcc-lcd.rules \
              $out/lib/udev/rules.d/99-trcc-lcd.rules

            # modprobe config
            install -Dm644 packaging/modprobe/trcc-lcd.conf \
              $out/etc/modprobe.d/trcc-lcd.conf

            # modules-load
            install -Dm644 packaging/modprobe/trcc-sg.conf \
              $out/etc/modules-load.d/trcc-sg.conf

            # desktop entry
            install -Dm644 src/trcc/assets/trcc-linux.desktop \
              $out/share/applications/trcc-linux.desktop

            # polkit policy
            install -Dm644 src/trcc/assets/com.github.lexonight1.trcc.policy \
              $out/share/polkit-1/actions/com.github.lexonight1.trcc.policy
          '';

          meta = with pkgs.lib; {
            description = "Thermalright LCD/LED Control Center for Linux";
            homepage = "https://github.com/Lexonight1/thermalright-trcc-linux";
            license = licenses.gpl3Plus;
            platforms = platforms.linux;
            maintainers = [ ];
          };
        };

        # Dev shell for contributors
        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (ps: with ps; [
              pyside6 numpy psutil pyusb pyudev click typer fastapi uvicorn
              python-multipart prompt-toolkit sounddevice
              pytest pytest-cov pytest-xdist httpx nvidia-ml-py ruff
            ]))
            pkgs.portaudio
            pkgs.libusb1
            pkgs.p7zip
            pkgs.sg3_utils
            pkgs.ffmpeg
          ];
        };
      }
    ) // {
      # NixOS module for system-level integration
      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.programs.trcc-linux;
        in {
          options.programs.trcc-linux = {
            enable = lib.mkEnableOption "TRCC Linux — Thermalright LCD/LED Control Center";
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ self.packages.${pkgs.system}.default ];

            services.udev.extraRules = builtins.readFile ./packaging/udev/99-trcc-lcd.rules;

            boot.kernelModules = [ "sg" ];

            boot.extraModprobeConfig = ''
              options usb-storage quirks=0402:3922:u,0416:5406:u,87cd:70db:u
            '';
          };
        };
    };
}

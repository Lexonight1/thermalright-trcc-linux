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
          version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
          pyproject = true;

          src = ./.;

          build-system = [ python.pkgs.hatchling ];

          dependencies = with python.pkgs; [
            pyside6
            numpy
            psutil
            pyusb
            pyudev          # Linux hotplug (live attach/detach + coldplug) — #139
            hidapi          # HID output-report transport — some firmware ignores
                            # libusb bulk and only accepts HID reports (#228)
            click
            typer
            fastapi
            cryptography
            uvicorn
            python-multipart
            prompt-toolkit
            sounddevice
            certifi
            nvidia-ml-py    # NVIDIA GPU temp/usage/clock — always on for NixOS
                            # users (the reader self-guards on non-NVIDIA hosts;
                            # NixOS can't `pip install` the optional extra) — #207
          ];

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
            install -Dm644 packaging/udev/70-trcc-rgb.rules \
              $out/lib/udev/rules.d/70-trcc-rgb.rules

            # modprobe config
            install -Dm644 packaging/modprobe/trcc-lcd.conf \
              $out/etc/modprobe.d/trcc-lcd.conf

            # modules-load
            install -Dm644 packaging/modprobe/trcc-sg.conf \
              $out/etc/modules-load.d/trcc-sg.conf
            install -Dm644 packaging/modprobe/trcc-rapl.conf \
              $out/etc/modules-load.d/trcc-rapl.conf

            # desktop entry
            install -Dm644 src/trcc/assets/trcc-linux.desktop \
              $out/share/applications/trcc-linux.desktop

            # polkit policy
            install -Dm644 src/trcc/assets/com.github.lexonight1.trcc.policy \
              $out/share/polkit-1/actions/com.github.lexonight1.trcc.policy

            # privileged helpers (pkexec targets): MCHBAR timings, DRAM slots,
            # RAM-lighting access
            install -Dm755 src/trcc/assets/trcc-imc $out/bin/trcc-imc
            install -Dm755 src/trcc/assets/trcc-dmi $out/bin/trcc-dmi
            install -Dm755 src/trcc/assets/trcc-ram-access $out/bin/trcc-ram-access
            install -Dm755 src/trcc/assets/trcc-ram-access-off $out/bin/trcc-ram-access-off
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
              pyside6 numpy psutil pyusb pyudev hidapi click typer fastapi cryptography uvicorn
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
            # Opt-in, as on every distro: the chipset SMBus also carries the
            # memory's own settings chips, and Linux grants a whole bus, so
            # every program the seat user runs could reach them.  /etc is
            # read-only here, so `trcc system ram-lighting enable` cannot
            # write this rule -- it is declared instead, and TRCC reads it.
            ramLighting = lib.mkEnableOption
              "RGB memory lighting: the user at the seat may open the chipset SMBus";
          };

          config = lib.mkIf cfg.enable (lib.mkMerge [ {
            environment.systemPackages = [ self.packages.${pkgs.system}.default ];

            # Read the shipped rules but rewrite the RAPL rule's `/bin/chmod`
            # (fine on FHS distros) to the Nix store path — NixOS has no
            # `/bin/chmod`, and its udev-rules builder rejects absolute-path
            # programs that don't exist, failing the whole system build (#207).
            services.udev.extraRules =
              builtins.replaceStrings
                [ "/bin/chmod" ] [ "${pkgs.coreutils}/bin/chmod" ]
                (builtins.readFile ./packaging/udev/99-trcc-lcd.rules);

            # The motherboard RGB rule tags uaccess, which 73-seat-late.rules
            # applies -- extraRules land in 99-local.rules, too late, so it
            # goes in as its own 70- rule package.
            services.udev.packages = [
              (pkgs.writeTextDir "lib/udev/rules.d/70-trcc-rgb.rules"
                (builtins.readFile ./packaging/udev/70-trcc-rgb.rules))
            ];

            boot.kernelModules = [ "sg" ];

            boot.extraModprobeConfig = ''
              options usb-storage quirks=0402:3922:u,0416:5406:u,87cd:70db:u
            '';
          }
          (lib.mkIf cfg.ramLighting {
            # A 70- rule package, like the RGB rule: uaccess must land before
            # 73-seat-late.rules.  i2c-dev creates the bus's device file.
            services.udev.packages = [
              (pkgs.writeTextDir "lib/udev/rules.d/70-trcc-ram-lighting.rules"
                (builtins.readFile ./packaging/nixos/70-trcc-ram-lighting.rules))
            ];
            boot.kernelModules = [ "i2c-dev" ];
          }) ]);
        };
    };
}

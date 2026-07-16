{
  description = "matebot — the proactive companion for GaggiMate espresso machines";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ];
    in
    {
      packages = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in rec {
          default = matebot;
          matebot = pkgs.python3Packages.buildPythonApplication {
            pname = "matebot";
            version = "0.3.0";
            pyproject = true;
            src = self;
            build-system = [ pkgs.python3Packages.hatchling ];
            dependencies = with pkgs.python3Packages; [
              aiohttp
              matplotlib
              python-telegram-bot
              # discord.py is optional at runtime; add it when using the discord messenger
            ] ++ pkgs.lib.optional (pkgs.python3Packages ? discordpy) pkgs.python3Packages.discordpy;
            nativeCheckInputs = with pkgs.python3Packages; [ pytestCheckHook pytest-asyncio ];
            pythonImportsCheck = [ "matebot" ];
          };
        });

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.matebot;
          pkg = self.packages.${pkgs.system}.default;
        in
        {
          options.services.matebot = {
            enable = lib.mkEnableOption "matebot, the GaggiMate shot companion";
            machineHost = lib.mkOption {
              type = lib.types.str;
              description = "Hostname/IP of the GaggiMate controller.";
            };
            messenger = lib.mkOption {
              type = lib.types.enum [ "telegram" "discord" ];
              default = "telegram";
            };
            environmentFile = lib.mkOption {
              type = lib.types.path;
              description = "EnvironmentFile with TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID (or DISCORD_*).";
            };
            dataRepo = lib.mkOption {
              type = lib.types.nullOr lib.types.path;
              default = null;
              description = "Git-backed shot journal to sync after each shot (null = off).";
            };
            user = lib.mkOption {
              type = lib.types.str;
              default = "matebot";
              description = "User to run as (needs git+ssh identity when dataRepo is set).";
            };
            stateDir = lib.mkOption {
              type = lib.types.str;
              default = "/var/lib/matebot";
            };
            wakeHook = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Shell command that powers the machine's smart plug ON (used by /wake).";
            };
            sleepHook = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Shell command that powers the smart plug OFF (used by /sleep).";
            };
            minShotDuration = lib.mkOption { type = lib.types.int; default = 10; };
            ignoreProfiles = lib.mkOption {
              type = lib.types.str;
              default = "(?i)backflush|descale|flush|clean";
            };
          };

          config = lib.mkIf cfg.enable {
            users.users = lib.mkIf (cfg.user == "matebot") {
              matebot = { isSystemUser = true; group = "matebot"; home = cfg.stateDir; };
            };
            users.groups = lib.mkIf (cfg.user == "matebot") { matebot = { }; };

            systemd.services.matebot = {
              description = "matebot — GaggiMate shot companion";
              wantedBy = [ "multi-user.target" ];
              after = [ "network-online.target" ];
              wants = [ "network-online.target" ];
              path = [ pkgs.git pkgs.openssh pkgs.ffmpeg-headless ];
              environment = {
                MATEBOT_MACHINE_HOST = cfg.machineHost;
                MATEBOT_MESSENGER = cfg.messenger;
                MATEBOT_STATE_DIR = cfg.stateDir;
                MATEBOT_MIN_SHOT_S = toString cfg.minShotDuration;
                MATEBOT_IGNORE_PROFILES = cfg.ignoreProfiles;
              } // lib.optionalAttrs (cfg.wakeHook != "") {
                MATEBOT_WAKE_HOOK = cfg.wakeHook;
              } // lib.optionalAttrs (cfg.sleepHook != "") {
                MATEBOT_SLEEP_HOOK = cfg.sleepHook;
              } // lib.optionalAttrs (cfg.dataRepo != null) {
                MATEBOT_DATA_REPO = toString cfg.dataRepo;
                MATEBOT_SYNC = "1";
              };
              serviceConfig = {
                ExecStart = "${pkg}/bin/matebot run";
                EnvironmentFile = cfg.environmentFile;
                User = cfg.user;
                StateDirectory = lib.mkIf (cfg.stateDir == "/var/lib/matebot") "matebot";
                Restart = "always";
                RestartSec = "10";
                NoNewPrivileges = true;
                PrivateTmp = true;
              };
            };
          };
        };

      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: with ps; [
                aiohttp
                python-telegram-bot
                pytest
                pytest-asyncio
              ]))
              pkgs.ruff
            ];
          };
        });
    };
}

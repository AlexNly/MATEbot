{
  description = "gaggibot — the proactive companion for GaggiMate espresso machines";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ];
    in
    {
      packages = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in rec {
          default = gaggibot;
          gaggibot = pkgs.python3Packages.buildPythonApplication {
            pname = "gaggibot";
            version = "0.1.0";
            pyproject = true;
            src = self;
            build-system = [ pkgs.python3Packages.hatchling ];
            dependencies = with pkgs.python3Packages; [
              aiohttp
              python-telegram-bot
              # discord.py is optional at runtime; add it when using the discord messenger
            ] ++ pkgs.lib.optional (pkgs.python3Packages ? discordpy) pkgs.python3Packages.discordpy;
            nativeCheckInputs = with pkgs.python3Packages; [ pytestCheckHook pytest-asyncio ];
            pythonImportsCheck = [ "gaggibot" ];
          };
        });

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.gaggibot;
          pkg = self.packages.${pkgs.system}.default;
        in
        {
          options.services.gaggibot = {
            enable = lib.mkEnableOption "gaggibot, the GaggiMate shot companion";
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
              default = "gaggibot";
              description = "User to run as (needs git+ssh identity when dataRepo is set).";
            };
            stateDir = lib.mkOption {
              type = lib.types.str;
              default = "/var/lib/gaggibot";
            };
            minShotDuration = lib.mkOption { type = lib.types.int; default = 10; };
            ignoreProfiles = lib.mkOption {
              type = lib.types.str;
              default = "(?i)backflush|descale|flush|clean";
            };
          };

          config = lib.mkIf cfg.enable {
            users.users = lib.mkIf (cfg.user == "gaggibot") {
              gaggibot = { isSystemUser = true; group = "gaggibot"; home = cfg.stateDir; };
            };
            users.groups = lib.mkIf (cfg.user == "gaggibot") { gaggibot = { }; };

            systemd.services.gaggibot = {
              description = "gaggibot — GaggiMate shot companion";
              wantedBy = [ "multi-user.target" ];
              after = [ "network-online.target" ];
              wants = [ "network-online.target" ];
              path = [ pkgs.git pkgs.openssh ];
              environment = {
                GAGGIBOT_MACHINE_HOST = cfg.machineHost;
                GAGGIBOT_MESSENGER = cfg.messenger;
                GAGGIBOT_STATE_DIR = cfg.stateDir;
                GAGGIBOT_MIN_SHOT_S = toString cfg.minShotDuration;
                GAGGIBOT_IGNORE_PROFILES = cfg.ignoreProfiles;
              } // lib.optionalAttrs (cfg.dataRepo != null) {
                GAGGIBOT_DATA_REPO = toString cfg.dataRepo;
                GAGGIBOT_SYNC = "1";
              };
              serviceConfig = {
                ExecStart = "${pkg}/bin/gaggibot run";
                EnvironmentFile = cfg.environmentFile;
                User = cfg.user;
                StateDirectory = lib.mkIf (cfg.stateDir == "/var/lib/gaggibot") "gaggibot";
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

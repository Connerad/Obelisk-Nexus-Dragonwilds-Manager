from __future__ import annotations

import json
import tempfile
import os
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.models import ServerProfile
from app.storage import ProfileStore
from app.services.backup_service import BackupError, create_backup, restore_backup, verify_backup
from app.services.config_service import IniDocument, ensure_dedicated_config, discover_configs
from app.services.mod_service import ModInstallError, install_mod, uninstall_mod
from app.services.profile_service import create_profile_transaction, update_profile_transaction
from app.services.server_service import ServerService
from app.services.world_service import import_world, rename_world
from app.services.public_service import build_readiness_report, public_search_name, validate_world_name


class CoreTests(unittest.TestCase):
    def test_ini_preserves_comments_unknown_keys_and_section(self):
        text = "; comment\n[Other]\nAlpha=1\n\n[/Script/Dominion.DedicatedServerSettings]\nServerName=Old\nFutureKey=KeepMe\n"
        doc = IniDocument(text)
        doc.set("/Script/Dominion.DedicatedServerSettings", "ServerName", "New")
        doc.set("/Script/Dominion.DedicatedServerSettings", "DefaultWorldName", "World")
        out = doc.render()
        self.assertIn("; comment", out)
        self.assertIn("FutureKey=KeepMe", out)
        self.assertIn("[Other]\nAlpha=1", out)
        dedicated = out.split("[/Script/Dominion.DedicatedServerSettings]", 1)[1]
        self.assertIn("DefaultWorldName=World", dedicated)
        self.assertNotIn("DefaultWorldName=World", out.split("[/Script/Dominion.DedicatedServerSettings]", 1)[0])

    def test_profile_config_uses_documented_section_and_keys(self):
        with tempfile.TemporaryDirectory() as td:
            profile = ServerProfile(name="My Server", install_dir=str(Path(td) / "server"), world_name="My World", owner_id="owner", admin_password="admin", world_password="world", max_players=42, port=8999)
            ServerService.apply_profile_config(profile)
            text = profile.config_file.read_text(encoding="utf-8")
            self.assertIn("[/Script/Dominion.DedicatedServerSettings]", text)
            self.assertIn("OwnerId=owner", text)
            self.assertIn("DefaultWorldName=My World", text)
            self.assertNotIn("MaxPlayers", text)
            self.assertNotIn("\nPort=", text)

    def test_config_discovery_includes_server_and_mod_configs_without_content_scan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"
            server_cfg = root / "RSDragonwilds" / "Saved" / "Config" / "WindowsServer" / "DedicatedServer.ini"
            mod_cfg = root / "RSDragonwilds" / "Binaries" / "Win64" / "Mods" / "Example" / "config.lua"
            content_cfg = root / "RSDragonwilds" / "Content" / "Huge" / "not-a-manager-config.ini"
            for path in (server_cfg, mod_cfg, content_cfg):
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text("x=1", encoding="utf-8")
            found = discover_configs(root)
            self.assertIn(server_cfg.resolve(), found)
            self.assertIn(mod_cfg.resolve(), found)
            self.assertNotIn(content_cfg.resolve(), found)

    def test_backup_verify_and_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; root.mkdir()
            (root / "Saved").mkdir(); (root / "Saved" / "world.sav").write_bytes(b"world-v1")
            backups = root / "ManagerBackups"
            archive = create_backup(root, backups, [root / "Saved"], "full")
            self.assertTrue(verify_backup(archive)[0])
            (root / "Saved" / "world.sav").write_bytes(b"world-v2")
            restore_backup(root, archive, backups)
            self.assertEqual((root / "Saved" / "world.sav").read_bytes(), b"world-v1")

    def test_backup_empty_sources_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; root.mkdir()
            with self.assertRaises(BackupError):
                create_backup(root, root / "backups", [root / "missing"], "empty")

    def test_restore_rejects_checksum_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; root.mkdir(); (root / "a.txt").write_text("a")
            archive = create_backup(root, root / "backups", [root / "a.txt"])
            with archive.open("ab") as fh: fh.write(b"tamper")
            with self.assertRaises(BackupError):
                restore_backup(root, archive, root / "backups")

    def test_profile_creation_rolls_back_on_save_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "new-server"
            profile = ServerProfile(name="Test", install_dir=str(root), owner_id="owner", admin_password="admin")
            store = mock.Mock(spec=ProfileStore)
            store.save.side_effect = OSError("disk full")
            with self.assertRaises(OSError):
                create_profile_transaction(store, [], profile)
            self.assertFalse(root.exists())

    def test_steamcmd_zip_extraction_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "steamcmd.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("steamcmd.exe", b"fake")
                zf.writestr("linux32/steamclient.so", b"fake")
            executable = ServerService._extract_steamcmd_zip(archive, Path(td) / "tools")
            self.assertEqual(executable.name, "steamcmd.exe")
            self.assertTrue(executable.exists())

    def test_steamcmd_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "steamcmd.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../steamcmd.exe", b"fake")
            with self.assertRaises(RuntimeError):
                ServerService._extract_steamcmd_zip(archive, Path(td) / "tools")

    def test_launch_args_apply_profile_ports_and_player_limit(self):
        profile = ServerProfile(port=7781, query_port=27019, max_players=5, launch_args="-log -NewConsole")
        args = ServerService.build_launch_args(profile)
        self.assertIn("-Port=7781", args)
        self.assertIn("-QueryPort=27019", args)
        self.assertIn("-ini:Game:[/Script/Engine.GameSession]:MaxPlayers=5", args)
        self.assertIn("-MultiHome=0.0.0.0", args)

    def test_launch_args_respect_explicit_overrides(self):
        profile = ServerProfile(port=7781, query_port=27019, max_players=5, launch_args="-Port=9000 -QueryPort=29000 -ini:Game:[/Script/Engine.GameSession]:MaxPlayers=4")
        args = ServerService.build_launch_args(profile)
        self.assertEqual(sum(a.casefold().startswith("-port=") for a in args), 1)
        self.assertEqual(sum(a.casefold().startswith("-queryport=") for a in args), 1)
        self.assertEqual(sum("gamesession]:maxplayers=" in a.casefold() for a in args), 1)

    @unittest.skipIf(os.name == "nt", "Linux fake-process test")
    def test_server_start_stop_with_fake_executable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; root.mkdir()
            exe = root / "RSDragonwilds.exe"
            exe.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
            exe.chmod(0o755)
            profile = ServerProfile(name="Fake", install_dir=str(root), owner_id="owner", admin_password="admin", port=7788, launch_args="")
            service = ServerService()
            pid = service.start(profile)
            self.assertGreater(pid, 0)
            self.assertTrue(service.is_running(profile))
            self.assertTrue(profile.config_file.exists())
            service.stop(profile, timeout=1)
            self.assertFalse(service.is_running(profile))

    def test_profile_update_failure_keeps_original_and_rolls_back_new_root(self):
        with tempfile.TemporaryDirectory() as td:
            original_root = Path(td) / "original"
            original = ServerProfile(name="Original", install_dir=str(original_root), owner_id="owner", admin_password="admin", port=7777, query_port=27015)
            store = ProfileStore(Path(td) / "profiles.json")
            create_profile_transaction(store, [], original)
            candidate = ServerProfile.from_dict(original.to_dict())
            candidate.name = "Changed"
            candidate.install_dir = str(Path(td) / "new-root")
            candidate.port = 7778
            candidate.query_port = 27016
            with mock.patch.object(store, "save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    update_profile_transaction(store, [original], candidate)
            self.assertEqual(original.name, "Original")
            self.assertFalse(Path(candidate.install_dir).exists())
            self.assertTrue(original.config_file.exists())

    def test_profile_rejects_duplicate_query_port(self):
        with tempfile.TemporaryDirectory() as td:
            existing = ServerProfile(name="A", install_dir=str(Path(td)/"a"), owner_id="owner-a", admin_password="admin", port=7777, query_port=27015)
            candidate = ServerProfile(name="B", install_dir=str(Path(td)/"b"), owner_id="owner-b", admin_password="admin", port=7778, query_port=27015)
            with self.assertRaisesRegex(ValueError, "Query port"):
                create_profile_transaction(ProfileStore(Path(td)/"profiles.json"), [existing], candidate)

    def test_profile_store_encrypts_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profiles.json"
            store = ProfileStore(path)
            profile = ServerProfile(name="Secret", install_dir=str(Path(td)/"server"), admin_password="admin-secret", world_password="world-secret", discord_webhook="https://discord.invalid/secret")
            store.save([profile])
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("admin-secret", raw)
            self.assertNotIn("world-secret", raw)
            self.assertNotIn("discord.invalid/secret", raw)
            loaded = store.load()[0]
            self.assertEqual(loaded.admin_password, "admin-secret")
            self.assertEqual(loaded.world_password, "world-secret")

    @unittest.skipIf(os.name == "nt", "Linux fake-process test")
    def test_two_server_instances_run_independently(self):
        with tempfile.TemporaryDirectory() as td:
            service = ServerService()
            profiles = []
            for index in range(2):
                root = Path(td) / f"server-{index}"; root.mkdir()
                exe = root / "RSDragonwilds.exe"
                exe.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8"); exe.chmod(0o755)
                profiles.append(ServerProfile(name=f"S{index}", install_dir=str(root), owner_id=f"owner-{index}", admin_password="admin", port=7777+index, query_port=27015+index, launch_args=""))
            try:
                service.start(profiles[0]); service.start(profiles[1])
                self.assertTrue(service.is_running(profiles[0]))
                self.assertTrue(service.is_running(profiles[1]))
                self.assertNotEqual(profiles[0].process_id, profiles[1].process_id)
            finally:
                for profile in profiles:
                    service.stop(profile, timeout=1, force=True)

    def test_world_replacement_stops_if_backup_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; save = root / "RSDragonwilds" / "Saved" / "SaveGames"; save.mkdir(parents=True)
            target = save / "World.sav"; target.write_bytes(b"old")
            source = Path(td) / "new.sav"; source.write_bytes(b"new")
            with mock.patch("app.services.world_service.create_backup", side_effect=OSError("backup drive unavailable")):
                with self.assertRaises(OSError):
                    import_world(root, save, source, root / "backups", "World.sav")
            self.assertEqual(target.read_bytes(), b"old")

    def test_mod_install_conflict_and_uninstall(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; root.mkdir()
            backups = root / "backups"
            a = Path(td) / "a.zip"
            with zipfile.ZipFile(a, "w") as zf: zf.writestr("A.pak", b"A")
            manifest = install_mod(root, backups, a, 1, "A", "1.0")
            target = root / "RSDragonwilds" / "Content" / "Paks" / "~mods" / "A.pak"
            self.assertTrue(target.exists())
            b = Path(td) / "b.zip"
            with zipfile.ZipFile(b, "w") as zf: zf.writestr("A.pak", b"B")
            with self.assertRaises(ModInstallError):
                install_mod(root, backups, b, 2, "B", "1.0")
            self.assertEqual(target.read_bytes(), b"A")
            uninstall_mod(root, backups, 1)
            self.assertFalse(target.exists())
            self.assertFalse(manifest.exists())

    def test_mod_archive_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; root.mkdir()
            archive = Path(td) / "link.zip"
            info = zipfile.ZipInfo("escape.pak")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(info, "../../outside")
            with self.assertRaises(ModInstallError):
                install_mod(root, root / "backups", archive, 9, "Link", "1")

    def test_mod_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"; root.mkdir()
            archive = Path(td) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf: zf.writestr("../evil.pak", b"x")
            with self.assertRaises(ModInstallError):
                install_mod(root, root / "backups", archive, 3, "Bad", "1")
            self.assertFalse((Path(td) / "evil.pak").exists())

    def test_public_search_name_uses_newest_save_filename(self):
        with tempfile.TemporaryDirectory() as td:
            profile = ServerProfile(name="Server", install_dir=str(Path(td) / "server"), owner_id="owner", admin_password="admin", world_name="Default")
            profile.save_dir.mkdir(parents=True, exist_ok=True)
            older = profile.save_dir / "Older.sav"; older.write_bytes(b"old")
            time.sleep(0.02)
            newest = profile.save_dir / "ExactPublicName.sav"; newest.write_bytes(b"new")
            self.assertEqual(public_search_name(profile), "ExactPublicName")

    def test_public_world_name_limit(self):
        self.assertEqual(validate_world_name("PublicWorld"), "PublicWorld")
        with self.assertRaisesRegex(ValueError, "16 characters"):
            validate_world_name("ThisWorldNameIsTooLong")

    def test_public_readiness_detects_exact_config_and_save(self):
        with tempfile.TemporaryDirectory() as td:
            profile = ServerProfile(name="My Server", install_dir=str(Path(td) / "server"), owner_id="owner", admin_password="admin", world_name="Default")
            profile.save_dir.mkdir(parents=True, exist_ok=True)
            (profile.save_dir / "LiveWorld.sav").write_bytes(b"save")
            ServerService.apply_profile_config(profile)
            checks, exact = build_readiness_report(profile)
            self.assertEqual(exact, "LiveWorld")
            config = next(c for c in checks if c.label == "DedicatedServer.ini")
            self.assertTrue(config.ok)

    def test_rename_world_creates_backup_and_changes_public_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "server"
            profile = ServerProfile(name="Server", install_dir=str(root), owner_id="owner", admin_password="admin")
            profile.save_dir.mkdir(parents=True, exist_ok=True)
            world = profile.save_dir / "Old.sav"; world.write_bytes(b"save")
            renamed = rename_world(root, world, root / "backups", "PublicWorld")
            self.assertTrue(renamed.exists())
            self.assertEqual(renamed.name, "PublicWorld.sav")
            self.assertTrue(any((root / "backups").glob("*.zip")))

    def test_campaign_profile_creates_empty_world_folder_for_first_launch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "campaign"
            profile = ServerProfile(
                name="Campaign", server_type="Campaign / Standard", install_dir=str(root),
                world_name="CampaignWorld", owner_id="owner", admin_password="admin",
            )
            store = ProfileStore(Path(td) / "profiles.json")
            create_profile_transaction(store, [], profile)
            self.assertTrue(profile.save_dir.exists())
            self.assertEqual(list(profile.save_dir.glob("*.sav")), [])

    def test_creative_profile_requires_real_save_instead_of_universal_empty_world(self):
        with tempfile.TemporaryDirectory() as td:
            profile = ServerProfile(
                name="Creative", server_type="Creative", install_dir=str(Path(td) / "creative"),
                world_name="CreativeWorld", owner_id="owner", admin_password="admin",
            )
            with self.assertRaisesRegex(ValueError, "cannot be created as an empty universal world"):
                create_profile_transaction(ProfileStore(Path(td) / "profiles.json"), [], profile)

    def test_custom_profile_imports_selected_save_with_public_name(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source-custom.sav"
            source.write_bytes(b"custom-world-data")
            profile = ServerProfile(
                name="Custom", server_type="Custom", install_dir=str(Path(td) / "custom"),
                world_name="PublicCustom", owner_id="owner", admin_password="admin",
            )
            create_profile_transaction(
                ProfileStore(Path(td) / "profiles.json"), [], profile, source
            )
            target = profile.save_dir / "PublicCustom.sav"
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"custom-world-data")

    def test_imported_profile_rolls_back_copied_world_when_profile_save_fails(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.sav"
            source.write_bytes(b"world")
            root = Path(td) / "imported"
            profile = ServerProfile(
                name="Imported", server_type="Imported / Other", install_dir=str(root),
                world_name="ImportedWorld", owner_id="owner", admin_password="admin",
            )
            store = mock.Mock(spec=ProfileStore)
            store.save.side_effect = OSError("disk full")
            with self.assertRaises(OSError):
                create_profile_transaction(store, [], profile, source)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()

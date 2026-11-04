"""Offline contract tests for the clients/features work: shared UI (localMedia
display, per-device media-details switch, events journal/settings), hash
routes, and the native local-notification stacks (Android 9-16, TV, iOS 17).
No network or device: these are source contract checks only. They do not
establish native compilation or runtime correctness; the separate browser,
Android instrumentation, and iOS simulator suites must also run. Hosted
validation is configured in mobile-release.yml.
"""
import plistlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(*parts):
    return (ROOT / Path(*(item for part in parts for item in (part if isinstance(part, (list, tuple)) else [part])))).read_text(encoding="utf-8")


ANDROID = "mobile/android/app/src/main/java/app/dubs/panel"
TV_MANIFEST = "mobile/android/tv/src/main/AndroidManifest.xml"


class SharedWebUITest(unittest.TestCase):
    def setUp(self):
        self.ui = read("src/ui.js")
        self.render = read("src/render.py")
        self.css = read("src/style.css")

    def test_media_details_switch_is_accessible_and_persisted(self):
        self.assertIn('id="media-details-switch"', self.render)
        self.assertIn('role="switch"', self.render)
        self.assertIn("Показывать качество и озвучку", self.render)
        self.assertIn("dubs-hide-media-details", self.ui)
        # Default ON: only an explicit "1" turns it off.
        self.assertIn('localStorage.getItem("dubs-hide-media-details") !== "1"', self.ui)
        self.assertIn(".preference-switch", self.css)

    def test_local_media_badges_are_honest(self):
        self.assertIn("Устарело", self.ui)
        self.assertIn("Не подтверждено", self.ui)
        self.assertIn("Проверено", self.ui)
        # multi-track expansion panel + per-track honesty about missing studio
        self.assertIn("media-panel", self.ui)
        self.assertIn("не указана в метаданных файла", self.ui)
        self.assertIn("aria-expanded", self.ui)
        self.assertIn("media-badge", self.css)
        self.assertIn("media-panel[hidden]", self.css)

    def test_no_html_injection_in_panel_script(self):
        self.assertNotIn(".innerHTML", self.ui)

    def test_routes_parse_safely_and_focus_rows(self):
        # strict numeric guard before any focus, honest notices otherwise
        self.assertIn("/^[1-9][0-9]{0,9}$/.test(ep)", self.ui)
        self.assertIn("pendingEpisode", self.ui)
        self.assertIn("new URLSearchParams", self.ui)
        self.assertIn("Серии нет в списке вышедших", self.ui)
        self.assertIn("Некорректный адрес серии", self.ui)

    def test_events_settings_split_account_from_device(self):
        self.assertIn('id="events-panel"', self.render)
        self.assertIn('id="events-account-enabled"', self.render)
        for etype in ("download_started", "torrent_complete", "library_ready",
                      "new_episode", "upgrade_started", "upgrade_result"):
            self.assertIn('data-event-type="%s"' % etype, self.render)
        self.assertIn('id="events-device-enabled"', self.render)
        self.assertIn("Это устройство", self.render)

    def test_browser_notifications_never_double_deliver(self):
        # native UA must skip the browser Notification API entirely
        self.assertIn("/DubsNative|DubsTV/.test(navigator.userAgent)", self.ui)
        native_pos = self.ui.index("NATIVE_APP")
        self.assertIn("Notification.requestPermission", self.ui)
        guard_pos = self.ui.index("if (NATIVE_APP) return;")
        self.assertLess(native_pos, self.ui.index("Notification.requestPermission"))
        self.assertLess(native_pos, guard_pos)

    def test_durable_cursor_and_dedupe_bounds(self):
        self.assertIn("dubs-events-cursor", self.ui)
        self.assertIn("dubs-events-instance", self.ui)
        self.assertIn("dubs-events-seen", self.ui)
        self.assertIn("slice(-200)", self.ui)
        # re-baseline on server instance change, never replay
        self.assertIn("/api/notifications/history?limit=1", self.ui)
        self.assertIn("/api/notifications/events?after=", self.ui)
        self.assertIn("/api/notifications/history?limit=30", self.ui)


class AndroidNativeTest(unittest.TestCase):
    def setUp(self):
        self.manifest = read("mobile/android/app/src/main/AndroidManifest.xml")
        self.tv = read(TV_MANIFEST)
        self.main = read(ANDROID + "/MainActivity.java")
        self.sync = read(ANDROID + "/NotificationSync.java")
        self.store = read(ANDROID + "/EventStore.java")
        self.gradle = read("mobile/android/app/build.gradle")
        self.tv_gradle = read("mobile/android/tv/build.gradle")

    def test_poll_service_hardening(self):
        for m in (self.manifest, self.tv):
            self.assertIn("android:exported=\"false\"", m)
            self.assertIn("android.permission.BIND_JOB_SERVICE", m)
            self.assertIn('android:name="app.dubs.panel.MainActivity"', m)
            self.assertIn("android.permission.POST_NOTIFICATIONS", m)
            self.assertIn("android.permission.RECEIVE_BOOT_COMPLETED", m)

    def test_no_js_bridge_and_immutable_intents(self):
        self.assertNotIn("addJavascriptInterface", self.main)
        self.assertIn("evaluateJavascript", self.main)
        self.assertIn("PendingIntent.FLAG_IMMUTABLE", self.sync)
        self.assertIn("dubs_fp", self.sync)
        self.assertIn("routeForTap", self.sync)

    def test_real_native_registration_apis_and_epoch_contract(self):
        self.assertIn("registrationMailboxScript", self.main)
        self.assertIn("pollRegistration", self.main)
        self.assertIn("REGISTRATION_POLLS", self.main)
        self.assertIn('getLongExtra("dubs_episode",0L)', self.main)
        self.assertIn("synchronized(EventStore.LOCK)", self.sync)
        self.assertIn('d.optString("instanceId"', self.sync)
        self.assertNotIn('optLong("instanceId"', self.sync + self.main + self.store)

    def test_encrypted_fail_closed_store(self):
        self.assertIn("AndroidKeyStore", self.store)
        self.assertIn("AES/GCM/NoPadding", self.store)
        self.assertNotIn("getExternalFilesDir", self.store)
        self.assertNotIn("MODE_WORLD", self.store)

    def test_poll_hygiene(self):
        self.assertIn("setInstanceFollowRedirects(false)", self.sync)
        self.assertIn("setPeriodic(15*60*1000L)", self.sync)
        self.assertIn("setPersisted(true)", self.sync)
        # write-ahead cursor before posting
        self.assertIn("s.setCursor(id);", self.sync)
        self.assertIn("s.save(c);", self.sync)
        self.assertIn("VISIBILITY_PUBLIC", self.sync)
        self.assertIn("BigTextStyle", self.sync)

    def test_local_delivery_only(self):
        ios = read("mobile/ios/Dubs/main.m")
        self.assertNotIn("registerForRemoteNotifications", ios)
        self.assertNotIn("FirebaseMessaging", self.sync)
        self.assertIn("UNNotificationRequest", ios)

    def test_build_overrides_keep_generic_defaults(self):
        self.assertIn("envOr('DUBS_APPLICATION_ID', 'app.dubs.panel')", self.gradle)
        self.assertIn("envOr('DUBS_PHONE_VERSION_CODE', '13')", self.gradle)
        self.assertIn("envOr('DUBS_PHONE_VERSION_NAME', '1.3.0')", self.gradle)
        self.assertIn("envOr('DUBS_TV_VERSION_CODE', '8')", self.tv_gradle)
        self.assertIn("envOr('DUBS_TV_VERSION_NAME', '1.5.0-tv')", self.tv_gradle)
        # applicationId override must not move the source activity class
        self.assertIn("app.dubs.panel.MainActivity", self.manifest)


class IOSNativeTest(unittest.TestCase):
    def setUp(self):
        self.main = read("mobile/ios/Dubs/main.m")
        self.plist = read("mobile/ios/Dubs/Info.plist")
        self.ent = read("mobile/ios/Dubs/Dubs.entitlements")
        self.project = read("mobile/ios/project.yml")

    def test_keychain_fail_closed(self):
        self.assertIn("SecItemAdd", self.main)
        self.assertIn("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly", self.main)
        self.assertIn("Keychain недоступен", self.main)

    def test_background_refresh_registered(self):
        self.assertIn("BGTaskScheduler", self.main)
        self.assertIn("15*60", self.main)
        self.assertIn("setTaskCompleted", self.main)

    def test_poll_rejects_redirects(self):
        self.assertIn("willPerformHTTPRedirection", self.main)
        self.assertIn("handler(nil)", self.main)

    def test_tap_route_validates_fingerprint(self):
        self.assertIn("EventsRoute", self.main)
        self.assertIn("fingerprintExpected", self.main)

    def test_actual_async_and_background_api_selectors(self):
        self.assertIn("callAsyncJavaScript:[DubsEvents registrationScript]", self.main)
        self.assertIn("usingQueue:dispatch_get_main_queue() launchHandler:", self.main)
        self.assertIn("setTaskCompletedWithSuccess:", self.main)
        self.assertNotIn("usingLaunchHandler:", self.main)
        self.assertIn("DubsRefreshCompletion", self.main)
        ent = plistlib.loads(self.ent.encode())
        self.assertEqual(ent['application-identifier'], '$(AppIdentifierPrefix)$(PRODUCT_BUNDLE_IDENTIFIER)')
        self.assertEqual(ent['keychain-access-groups'], [ent['application-identifier']])

    def test_plist_and_entitlements(self):
        self.assertIn("<string>1.3.0</string>", self.plist)
        self.assertIn("<string>13</string>", self.plist)
        self.assertIn("dubs.events.refresh", self.plist)
        self.assertIn("BGTaskSchedulerPermittedIdentifiers", self.plist)
        self.assertIn("UIBackgroundModes", self.plist)
        self.assertIn("<string>fetch</string>", self.plist)
        self.assertIn("keychain-access-groups", self.ent)
        self.assertNotIn("aps-environment", plistlib.loads(self.ent.encode()))
        self.assertIn("CODE_SIGN_ENTITLEMENTS: Dubs/Dubs.entitlements", self.project)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.wf = read(".github/workflows/mobile-release.yml")

    def test_dispatch_only_readonly_and_artifact_outputs(self):
        self.assertIn("workflow_dispatch:", self.wf)
        self.assertIn("permissions:", self.wf)
        self.assertIn("contents: read", self.wf)
        self.assertNotRegex(self.wf, r"gh release|tags:|releases/")
        self.assertIn("upload-artifact", self.wf)

    def test_emulator_matrix_and_ram(self):
        self.assertIn("api-level: 28", self.wf)
        self.assertIn("api-level: 36", self.wf)
        self.assertIn("ram-size: 4096M", self.wf)
        self.assertIn("connectedDebugAndroidTest", self.wf)

    def test_ios_simulator_and_unsigned_payload(self):
        self.assertIn("DUBS_IOS_SIMULATOR_ID", self.wf)
        self.assertIn("int(match[1]) >= 17", self.wf)
        self.assertIn("xcodegen generate", self.wf)
        self.assertIn("CODE_SIGNING_ALLOWED=NO build", self.wf)

    def test_plain_jvm_policy_tests_run_in_ci(self):
        self.assertIn("NotificationPolicyTest", self.wf)
        self.assertIn("ServerAddressTest", self.wf)


class SecretScanSafetyTest(unittest.TestCase):
    """Mirror the CI secret scan over every file this feature touches, so a
    regression fails here on dev hosts too (no private hosts/IPs/keys)."""

    ALLOW = re.compile(r"127\.0\.0\.1|0\.0\.0\.0|192\.0\.2\.|198\.51\.100\.|"
                       r"203\.0\.113\.|example\.org|localhost")
    BAD = re.compile(
        r"ratu\.sh|dubs\.pve|"
        r"(?<![\d.])10\.11\.\d+\.\d+|"
        r"(?<![\d.])192\.168\.\d+\.\d+|"
        r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----")

    PATHS = (
        ("src", "ui.js"), ("src", "style.css"), ("src", "render.py"),
        ("mobile", "android", "app", "src", "main", "AndroidManifest.xml"),
        (TV_MANIFEST.split("/")),
        (ANDROID.split("/"), "MainActivity.java"),
        (ANDROID.split("/"), "NotificationSync.java"),
        (ANDROID.split("/"), "NotificationSyncJob.java"),
        (ANDROID.split("/"), "EventStore.java"),
        ("mobile", "android", "app", "build.gradle"),
        ("mobile", "android", "tv", "build.gradle"),
        ("mobile", "ios", "Dubs", "main.m"),
        ("mobile", "ios", "Dubs", "Info.plist"),
        ("mobile", "ios", "Dubs", "Dubs.entitlements"),
        ("mobile", "ios", "project.yml"),
        ("mobile", "ios", "DubsUITests", "NotificationStateTests.m"),
        ("mobile", "tests", "NotificationPolicyTest.java"),
        ("mobile", "tests", "media-details-toggle.cjs"),
        (".github", "workflows", "mobile-release.yml"),
        ("docs", "mobile.md"),
    )

    def test_owned_files_pass_secret_scan(self):
        hits = []
        for path in self.PATHS:
            text = read(*path)
            for i, line in enumerate(text.splitlines(), 1):
                if self.BAD.search(line) and not self.ALLOW.search(line):
                    hits.append("%s:%d" % ("/".join(str(p) for p in path), i))
        self.assertEqual(hits, [], "secret-scan hits: %s" % hits)


if __name__ == "__main__":
    unittest.main()

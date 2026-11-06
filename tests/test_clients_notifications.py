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
    return (ROOT / Path(*parts)).read_text(encoding="utf-8")


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
            self.assertIn("android.permission.ACCESS_NETWORK_STATE", m)
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

    def test_android16_denial_and_scheduler_failure_contract(self):
        self.assertIn('permissionToOp("android.permission.POST_NOTIFICATIONS")', self.sync)
        self.assertIn('ops.checkOpNoThrow(op,android.os.Process.myUid(),c.getPackageName())', self.sync)
        self.assertIn('mode==AppOpsManager.MODE_IGNORED||mode==AppOpsManager.MODE_ERRORED', self.sync)
        self.assertNotIn('mode!=AppOpsManager.MODE_ALLOWED', self.sync)
        self.assertIn('nm.areNotificationsEnabled()', self.sync)
        self.assertIn('ch.getImportance()==NotificationManager.IMPORTANCE_NONE', self.sync)
        self.assertIn('==JobScheduler.RESULT_SUCCESS', self.sync)
        self.assertIn('catch(SecurityException denied)', self.sync)
        self.assertIn('if(!NotificationSync.backgroundScheduled(this))', self.main)
        # The real OS denial fixture must remain an AppOps denial with a runtime grant.
        device = read("mobile/android/app/src/androidTest/java/app/dubs/panel/NotificationsInstrumentedTest.java")
        self.assertIn('android.permission.POST_NOTIFICATIONS', device)
        self.assertIn('POST_NOTIFICATION \"+(allowed?"allow":"ignore")', device)
        self.assertRegex(device, r'assertFalse\("product reports active after OS denial: "\+diagnostics,NotificationSync\.notificationsAllowed\(context\)\)')

    def test_local_delivery_only(self):
        ios = read("mobile/ios/Dubs/main.m")
        self.assertNotIn("registerForRemoteNotifications", ios)
        self.assertNotIn("FirebaseMessaging", self.sync)
        self.assertIn("UNNotificationRequest", ios)

    def test_native_actions_are_settings_only(self):
        source=read("mobile/android/app/src/main/java/app/dubs/panel/MainActivity.java")
        self.assertNotIn('⚙ Сервер',source)
        self.assertNotIn('🔔',source)
        self.assertIn('native-server-settings',source)
        self.assertIn('native-events-settings',source)
        self.assertIn('DubsSettingsOnly/1',source)

    def test_build_overrides_keep_generic_defaults(self):
        self.assertIn("envOr('DUBS_APPLICATION_ID', 'app.dubs.panel')", self.gradle)
        self.assertIn("envOr('DUBS_PHONE_VERSION_CODE', '15')", self.gradle)
        self.assertIn("envOr('DUBS_PHONE_VERSION_NAME', '1.4.0')", self.gradle)
        self.assertIn("envOr('DUBS_TV_VERSION_CODE', '10')", self.tv_gradle)
        self.assertIn("envOr('DUBS_TV_VERSION_NAME', '1.5.2-tv')", self.tv_gradle)
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
        self.assertIn("registerForTaskWithIdentifier:EVENTS_TASK_ID usingQueue:dispatch_get_main_queue() launchHandler:", self.main)
        self.assertNotIn("registerTaskWithIdentifier:", self.main)
        self.assertIn("setTaskCompletedWithSuccess:", self.main)
        self.assertNotIn("usingLaunchHandler:", self.main)
        self.assertIn("DubsRefreshCompletion", self.main)
        ent = plistlib.loads(self.ent.encode())
        self.assertEqual(ent['application-identifier'], '$(AppIdentifierPrefix)$(PRODUCT_BUNDLE_IDENTIFIER)')
        self.assertEqual(ent['keychain-access-groups'], [ent['application-identifier']])

    def test_panel_script_selector_is_declared_before_typed_use(self):
        interface = self.main.index("@interface PanelController :")
        declaration = self.main.index("- (void)evaluateJavaScriptString:(NSString *)script;")
        use = self.main.index("[panel evaluateJavaScriptString:RevokeScript(deviceId)]")
        self.assertLess(interface, declaration)
        self.assertLess(declaration, use)
        self.assertNotIn("[(id)panel evaluateJavaScriptString:", self.main)

    def test_player_completion_captures_origin_and_generation_locally(self):
        player = self.main.split("- (void)openEpisode:(NSURL *)original{", 1)[1].split("- (BOOL)openPlayerURL:", 1)[0]
        callback = player.index("completionHandler:")
        self.assertLess(player.index("NSString *capturedOrigin=OriginString(self.serverURL);"), callback)
        self.assertLess(player.index("NSUInteger generation=EventsGeneration;"), callback)
        self.assertIn("generation!=EventsGeneration", player)
        self.assertIn("capturedOrigin isEqualToString:OriginString(self.serverURL)", player)

    def test_ios_shortcuts_are_settings_only_and_recovery_remains(self):
        source=read("mobile/ios/Dubs/main.m")
        self.assertNotIn('⚙ Сервер',source)
        self.assertNotIn('🔔',source)
        self.assertIn('native-server-settings',source)
        self.assertIn('native-events-settings',source)
        self.assertEqual(source.count('didFailProvisionalNavigation'),1)
        self.assertIn('DubsSettingsOnly/1',source)

    def test_plist_and_entitlements(self):
        self.assertIn("<string>1.4.0</string>", self.plist)
        self.assertIn("<string>14</string>", self.plist)
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

    def test_trusted_pr_dispatch_readonly_and_artifact_outputs(self):
        self.assertIn("workflow_dispatch:", self.wf)
        self.assertIn("permissions:", self.wf)
        self.assertIn("contents: read", self.wf)
        self.assertNotRegex(self.wf, r"gh release|tags:|releases/")
        self.assertIn("upload-artifact", self.wf)

    def test_fork_prs_cannot_start_jobs_or_access_signing_artifacts(self):
        self.assertIn("pull_request:", self.wf)
        self.assertIn("synchronize", self.wf)
        self.assertNotIn("pull_request_target", self.wf)
        guard = "if: github.event_name == 'workflow_dispatch' || github.event.pull_request.head.repo.full_name == github.repository"
        # All four jobs, including signing and artifact-producing iOS, are gated.
        self.assertEqual(self.wf.count(guard), 4)
        self.assertEqual(self.wf.count("persist-credentials: false"), 4)

    def test_pr_versions_and_production_id_overrides_have_explicit_fallbacks(self):
        for field, default in (("phone_version_code", "15"), ("phone_version_name", "1.4.0"),
                               ("tv_version_code", "10"), ("tv_version_name", "1.5.2-tv")):
            self.assertIn("inputs.%s || '%s'" % (field, default), self.wf)
        for name in ("DUBS_ANDROID_APPLICATION_ID", "DUBS_TV_APPLICATION_ID", "DUBS_IOS_BUNDLE_ID"):
            self.assertIn("vars." + name, self.wf)

    def test_emulator_matrix_and_ram(self):
        self.assertIn("api-level: 28", self.wf)
        self.assertIn("api-level: 36", self.wf)
        self.assertIn("ram-size: 4096M", self.wf)
        self.assertIn("connectedDebugAndroidTest", self.wf)

    def test_ios_simulator_and_verified_trollstore_ipa(self):
        self.assertIn("DUBS_IOS_SIMULATOR_ID", self.wf)
        self.assertIn("int(match[1]) >= 17", self.wf)
        self.assertIn("xcodegen generate", self.wf)
        self.assertIn("CODE_SIGNING_ALLOWED=NO build", self.wf)
        sign = "codesign --force --sign - --entitlements trollstore-entitlements.plist Payload/Dubs.app"
        verify = "codesign --verify --strict Payload/Dubs.app"
        extract = "codesign --display --entitlements :- Payload/Dubs.app > verified-entitlements.plist"
        self.assertLess(self.wf.index("cp -R build/release/Build/Products/Release-iphoneos/Dubs.app Payload/"), self.wf.index(sign))
        self.assertLess(self.wf.index(sign), self.wf.index(verify))
        self.assertLess(self.wf.index(verify), self.wf.index(extract))
        self.assertIn("assert entitlements == expected", self.wf)
        self.assertIn("architectures == ['arm64']", self.wf)
        self.assertIn("info['CFBundleSupportedPlatforms'] == ['iPhoneOS']", self.wf)
        self.assertIn("info['MinimumOSVersion'] == '17.0'", self.wf)
        self.assertIn("info['CFBundleShortVersionString'] == '1.4.0'", self.wf)
        self.assertIn("info['CFBundleVersion'] == '14'", self.wf)
        self.assertIn("zip -qry Dubs-1.4.0-TrollStore.ipa Payload", self.wf)
        self.assertIn("shasum -a 256 Dubs-1.4.0-TrollStore.ipa > Dubs-1.4.0-TrollStore.ipa.sha256.txt", self.wf)
        self.assertNotIn("dubs-ios-payload-unsigned.zip", self.wf)
        self.assertNotIn("parent signs", self.wf)


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
        "src/ui.js", "src/style.css", "src/render.py",
        "mobile/android/app/src/main/AndroidManifest.xml", TV_MANIFEST,
        *(ANDROID + "/" + name for name in ("MainActivity.java", "NotificationSync.java", "NotificationSyncJob.java", "EventStore.java")),
        "mobile/android/app/src/androidTest/java/app/dubs/panel/NotificationsInstrumentedTest.java",
        "mobile/android/app/build.gradle", "mobile/android/tv/build.gradle", "mobile/android/gradle.properties",
        "mobile/ios/Dubs/main.m", "mobile/ios/Dubs/Info.plist", "mobile/ios/Dubs/Dubs.entitlements",
        "mobile/ios/project.yml", "mobile/ios/DubsUITests/NotificationStateTests.m",
        "mobile/ios/DubsTests/NotificationCoreTests.m", "mobile/tests/NotificationPolicyTest.java",
        "mobile/tests/media-details-toggle.cjs", "mobile/tests/events-journal.cjs", "mobile/tests/render-fixture.py",
        ".github/workflows/mobile-release.yml", "docs/mobile.md",
    )

    def test_owned_files_pass_secret_scan(self):
        hits = []
        for path in self.PATHS:
            text = read(path)
            for i, line in enumerate(text.splitlines(), 1):
                if self.BAD.search(line) and not self.ALLOW.search(line):
                    hits.append("%s:%d" % (path, i))
        self.assertEqual(hits, [], "secret-scan hits: %s" % hits)


if __name__ == "__main__":
    unittest.main()

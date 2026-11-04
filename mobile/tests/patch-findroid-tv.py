from pathlib import Path
root=Path('companion')
p=root/'app/tv/build.gradle.kts';s=p.read_text().replace('applicationId = "dev.jdtech.jellyfin"','applicationId = "dev.jdtech.jellyfin.dubs.tv"');p.write_text(s)
p=root/'app/tv/src/main/AndroidManifest.xml';s=p.read_text().replace('android:allowBackup="true"','android:allowBackup="false"').replace('android:label="@string/app_name"','android:label="Findroid Dubs TV"').replace('android:name=".MainActivity"','android:name=".MainActivity" android:launchMode="singleTask"');s=s.replace('        </activity>','''            <intent-filter>
                <action android:name="android.intent.action.VIEW"/>
                <category android:name="android.intent.category.DEFAULT"/>
                <category android:name="android.intent.category.BROWSABLE"/>
                <data android:scheme="jellyfin" android:host="item"/>
            </intent-filter>
        </activity>''');p.write_text(s)
p=root/'app/tv/src/main/java/dev/jdtech/jellyfin/MainActivity.kt';s=p.read_text().replace('import android.os.Bundle','import android.content.Intent\nimport android.os.Bundle\nimport androidx.compose.runtime.mutableStateOf\nimport androidx.compose.runtime.setValue\nimport java.util.UUID');s=s.replace('    override fun onCreate', '''    private var requestedItemId by mutableStateOf<String?>(null)
    private fun readItemIntent(incoming: Intent): String? {
        val uri = incoming.data ?: return null
        if (incoming.action != Intent.ACTION_VIEW || uri.scheme != "jellyfin" || uri.host != "item" || uri.port != -1 || uri.userInfo != null || uri.query != null || uri.fragment != null) return null
        val value = uri.path?.removePrefix("/") ?: return null
        if (!Regex("[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}").matches(value)) return null
        return runCatching { UUID.fromString(value).toString() }.getOrNull()
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        requestedItemId = readItemIntent(intent)
    }
    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("requestedItemId", requestedItemId)
        super.onSaveInstanceState(outState)
    }
    override fun onCreate''');s=s.replace('        super.onCreate(savedInstanceState)','        super.onCreate(savedInstanceState)\n        requestedItemId = savedInstanceState?.getString("requestedItemId") ?: readItemIntent(intent)');s=s.replace('                        navController = navController,','                        navController = navController,\n                        requestedItemId = requestedItemId,\n                        onItemHandled = { requestedItemId = null },');p.write_text(s)
p=root/'app/tv/src/main/java/dev/jdtech/jellyfin/NavigationRoot.kt';s=p.read_text().replace('import androidx.compose.runtime.Composable','import androidx.compose.runtime.Composable\nimport androidx.compose.runtime.LaunchedEffect');s=s.replace('    navController: NavHostController,','    navController: NavHostController,\n    requestedItemId: String? = null,\n    onItemHandled: () -> Unit = {},',1);needle='    NavHost(navController = navController, startDestination = startDestination) {';assert needle in s;s=s.replace(needle,'''    LaunchedEffect(hasServers, hasCurrentServer, hasCurrentUser, requestedItemId) {
        if (hasServers && hasCurrentServer && hasCurrentUser && requestedItemId != null) {
            navController.navigate(PlayerRoute(itemId = requestedItemId, itemKind = BaseItemKind.EPISODE.serialName)) { launchSingleTop = true }
            onItemHandled()
        }
    }

'''+needle);p.write_text(s)

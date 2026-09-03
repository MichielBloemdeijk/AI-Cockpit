package nl.sander.aicockpit.jarvis

import android.Manifest
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.appfunctions.AppFunctionsProbe
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.data.JarvisAssistantStatus
import nl.sander.aicockpit.jarvis.notifications.JarvisEventStream
import nl.sander.aicockpit.jarvis.wake.WakeForegroundService
import nl.sander.aicockpit.jarvis.web.JarvisWebViewActivity

class MainActivity : ComponentActivity() {
    private lateinit var store: CredentialStore
    private lateinit var api: CockpitApi
    private var assistantSelected by mutableStateOf(false)

    private val permissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { }
    private val assistantRole = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { assistantSelected = isAssistantSelected() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = CredentialStore(this)
        api = CockpitApi(store)
        assistantSelected = isAssistantSelected()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(Modifier.fillMaxSize()) { JarvisSetup() }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        assistantSelected = isAssistantSelected()
        if (store.credential() != null) lifecycleScope.launch {
            runCatching { api.updateDevice(JarvisEventStream.lockState(this@MainActivity), store.eventCursor) }
        }
    }

    @Composable
    private fun JarvisSetup() {
        var serverUrl by remember { mutableStateOf(store.serverUrl) }
        var code by remember { mutableStateOf("") }
        var status by remember { mutableStateOf(if (store.credential() == null) "Not paired" else "Paired") }
        var wakeAfterBoot by remember { mutableStateOf(store.wakeAfterBoot) }
        var assistOptIn by remember { mutableStateOf(store.assistStructureOptIn) }
        var appFunctions by remember { mutableStateOf(store.appFunctionsEnabled) }
        var assistantHealth by remember { mutableStateOf<JarvisAssistantStatus?>(null) }
        val scope = rememberCoroutineScope()
        val paired = store.credential() != null

        LaunchedEffect(paired) {
            if (paired) {
                runCatching { api.assistantStatus() }
                    .onSuccess { assistantHealth = it }
                    .onFailure { status = it.message ?: "Unable to load assistant status" }
            } else assistantHealth = null
        }

        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("Jarvis for Android 17", style = MaterialTheme.typography.headlineSmall)
            Text("Native invocation and local wake; AI Cockpit remains the durable source of truth.")

            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("1. Pair this phone", style = MaterialTheme.typography.titleMedium)
                    OutlinedTextField(
                        value = serverUrl,
                        onValueChange = { serverUrl = it },
                        label = { Text("AI Cockpit URL") },
                        placeholder = { Text("http://100.x.x.x:3000") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = code,
                        onValueChange = { code = it.filter(Char::isDigit).take(12) },
                        label = { Text("Pairing code") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Button(
                        enabled = serverUrl.isNotBlank() && code.length >= 6,
                        onClick = {
                            scope.launch {
                                status = "Pairing…"
                                runCatching { api.pair(serverUrl, code, "${Build.MANUFACTURER} ${Build.MODEL}") }
                                    .onSuccess { result ->
                                        store.deviceId = result.deviceId
                                        store.saveCredential(result.credential)
                                        store.eventCursor = result.eventCursor
                                        store.sleep = false
                                        (application as JarvisApplication).eventStream.start()
                                        runCatching { api.updateDevice(JarvisEventStream.lockState(this@MainActivity)) }
                                        status = "Paired securely"
                                    }
                                    .onFailure { status = it.message ?: "Pairing failed" }
                            }
                        },
                    ) { Text(if (paired) "Pair again" else "Pair") }
                    Text(status, color = MaterialTheme.colorScheme.primary)
                }
            }

            if (paired) Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Current assistant status", style = MaterialTheme.typography.titleMedium)
                    val health = assistantHealth
                    if (health == null) {
                        Text("Loading…", style = MaterialTheme.typography.bodySmall)
                    } else {
                        Text(
                            "Worker ${health.workerStatus}",
                            color = if (health.workerStatus == "online") MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                        )
                        if (health.waitingForWorkerCount > 0) {
                            Text("${health.waitingForWorkerCount} task${if (health.waitingForWorkerCount == 1) "" else "s"} waiting for the worker")
                        }
                        if (health.overdueReminderCount > 0) {
                            Text("${health.overdueReminderCount} overdue reminder${if (health.overdueReminderCount == 1) "" else "s"}", color = MaterialTheme.colorScheme.error)
                        }
                        if (health.degradedComponents.isNotEmpty()) {
                            Text("Degraded: ${health.degradedComponents.joinToString { it.replace('_', ' ') }}", color = MaterialTheme.colorScheme.error)
                        } else {
                            Text("Runtime components healthy", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    OutlinedButton(onClick = {
                        scope.launch {
                            runCatching { api.assistantStatus() }
                                .onSuccess { assistantHealth = it }
                                .onFailure { status = it.message ?: "Unable to load assistant status" }
                        }
                    }) { Text("Refresh status") }
                }
            }

            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("2. Make Jarvis the assistant", style = MaterialTheme.typography.titleMedium)
                    Text(if (assistantSelected) "Jarvis is the selected system assistant." else "Select Jarvis for supported gestures and assistant buttons.")
                    Button(onClick = { requestAssistantRole() }) {
                        Text(if (assistantSelected) "Review assistant role" else "Select Jarvis")
                    }
                }
            }

            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("3. Listening and privacy", style = MaterialTheme.typography.titleMedium)
                    SettingRow("Resume wake listening after reboot", wakeAfterBoot) {
                        wakeAfterBoot = it; store.wakeAfterBoot = it
                    }
                    SettingRow("Allow foreground-app assist structure", assistOptIn) {
                        assistOptIn = it; store.assistStructureOptIn = it
                    }
                    Text("Screenshots are always disabled. Assist structure is used transiently and is not retained.", style = MaterialTheme.typography.bodySmall)
                    SettingRow("Probe experimental AppFunctions", appFunctions) {
                        appFunctions = it; store.appFunctionsEnabled = it
                    }
                    Text(AppFunctionsProbe.status(this@MainActivity), style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Button(enabled = paired, onClick = { startWake() }, modifier = Modifier.weight(1f)) { Text("Start wake") }
                        OutlinedButton(onClick = {
                            store.sleep = true
                            stopService(Intent(this@MainActivity, WakeForegroundService::class.java))
                        }, modifier = Modifier.weight(1f)) { Text("Sleep") }
                    }
                }
            }

            Button(
                enabled = serverUrl.isNotBlank(),
                onClick = {
                    store.serverUrl = serverUrl
                    startActivity(Intent(this@MainActivity, JarvisWebViewActivity::class.java))
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Open AI Cockpit") }
        }
    }

    @Composable
    private fun SettingRow(label: String, checked: Boolean, onChecked: (Boolean) -> Unit) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(label, modifier = Modifier.weight(1f).padding(end = 12.dp))
            Switch(checked = checked, onCheckedChange = onChecked)
        }
    }

    private fun requestAssistantRole() {
        val manager = getSystemService(RoleManager::class.java)
        if (manager.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) {
            assistantRole.launch(manager.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT))
        }
    }

    private fun isAssistantSelected(): Boolean {
        val manager = getSystemService(RoleManager::class.java)
        return manager.isRoleAvailable(RoleManager.ROLE_ASSISTANT) && manager.isRoleHeld(RoleManager.ROLE_ASSISTANT)
    }

    private fun startWake() {
        val required = buildList {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (required.isNotEmpty()) { permissions.launch(required.toTypedArray()); return }
        store.sleep = false
        ContextCompat.startForegroundService(
            this,
            Intent(this, WakeForegroundService::class.java).setAction(WakeForegroundService.ACTION_START),
        )
    }
}

package com.luminus.local.luminus

import android.app.Activity
import android.content.Intent
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterActivity() {
    private val channelName = "com.luminus.local.luminus/update"
    private var pendingExportResult: MethodChannel.Result? = null
    private var pendingExportPath: String? = null
    private var pendingImportResult: MethodChannel.Result? = null

    companion object {
        private const val REQ_EXPORT = 7001
        private const val REQ_IMPORT = 7002
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "cacheDir" -> result.success(cacheDir.absolutePath)
                    "installApk" -> {
                        val path = call.argument<String>("path")
                        if (path.isNullOrBlank()) {
                            result.error("bad_args", "Caminho do APK ausente.", null)
                            return@setMethodCallHandler
                        }
                        val apk = File(path)
                        if (!apk.exists()) {
                            result.error("missing_apk", "APK nao encontrado.", null)
                            return@setMethodCallHandler
                        }
                        try {
                            val uri = FileProvider.getUriForFile(
                                this,
                                "$packageName.fileprovider",
                                apk,
                            )
                            val intent = Intent(Intent.ACTION_VIEW).apply {
                                setDataAndType(uri, "application/vnd.android.package-archive")
                                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            }
                            startActivity(intent)
                            result.success(true)
                        } catch (error: Exception) {
                            result.error("install_failed", error.message, null)
                        }
                    }
                    "exportFile" -> {
                        if (pendingExportResult != null || pendingImportResult != null) {
                            result.error(
                                "busy",
                                "Ja existe uma operacao de arquivo em andamento.",
                                null,
                            )
                            return@setMethodCallHandler
                        }
                        val path = call.argument<String>("path")
                        if (path.isNullOrBlank() || !File(path).exists()) {
                            result.error("missing_file", "Arquivo para exportar nao encontrado.", null)
                            return@setMethodCallHandler
                        }
                        val name = call.argument<String>("name")
                            ?.takeIf { it.isNotBlank() }
                            ?: "luminus-world-pack.json"
                        val mime = call.argument<String>("mime")
                            ?.takeIf { it.isNotBlank() }
                            ?: "application/json"
                        pendingExportResult = result
                        pendingExportPath = path
                        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = mime
                            putExtra(Intent.EXTRA_TITLE, name)
                        }
                        try {
                            startActivityForResult(intent, REQ_EXPORT)
                        } catch (error: Exception) {
                            pendingExportResult = null
                            pendingExportPath = null
                            result.error("export_failed", error.message, null)
                        }
                    }
                    "pickJson" -> {
                        if (pendingExportResult != null || pendingImportResult != null) {
                            result.error(
                                "busy",
                                "Ja existe uma operacao de arquivo em andamento.",
                                null,
                            )
                            return@setMethodCallHandler
                        }
                        pendingImportResult = result
                        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = "*/*"
                            putExtra(
                                Intent.EXTRA_MIME_TYPES,
                                arrayOf(
                                    "application/json",
                                    "text/plain",
                                    "application/octet-stream",
                                ),
                            )
                        }
                        try {
                            startActivityForResult(intent, REQ_IMPORT)
                        } catch (error: Exception) {
                            pendingImportResult = null
                            result.error("import_failed", error.message, null)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQ_EXPORT -> handleExportResult(resultCode, data)
            REQ_IMPORT -> handleImportResult(resultCode, data)
        }
    }

    private fun handleExportResult(resultCode: Int, data: Intent?) {
        val result = pendingExportResult ?: return
        val path = pendingExportPath
        pendingExportResult = null
        pendingExportPath = null
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null || path == null) {
            result.success(null)
            return
        }
        Thread {
            try {
                File(path).inputStream().use { input ->
                    val output = contentResolver.openOutputStream(uri)
                        ?: throw IllegalStateException("Nao foi possivel abrir o destino.")
                    output.use { out -> input.copyTo(out) }
                }
                runOnUiThread { result.success(uri.toString()) }
            } catch (error: Exception) {
                runOnUiThread { result.error("export_failed", error.message, null) }
            }
        }.start()
    }

    private fun handleImportResult(resultCode: Int, data: Intent?) {
        val result = pendingImportResult ?: return
        pendingImportResult = null
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) {
            result.success(null)
            return
        }
        Thread {
            try {
                val target = File(cacheDir, "luminus-world-pack-import.json")
                val input = contentResolver.openInputStream(uri)
                    ?: throw IllegalStateException("Nao foi possivel abrir o arquivo.")
                input.use { source ->
                    target.outputStream().use { out -> source.copyTo(out) }
                }
                runOnUiThread { result.success(target.absolutePath) }
            } catch (error: Exception) {
                runOnUiThread { result.error("import_failed", error.message, null) }
            }
        }.start()
    }
}
